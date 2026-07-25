import hashlib
import os
from functools import wraps

import psycopg2
from flask import Blueprint, request, jsonify, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_login_identifier,
    business_by_id, replies_today_count, update_business, get_subscription_status,
    effective_subscription_active, has_pro_access, business_stats, log_event, save_push_token, log_client_error,
    add_business, delete_business, set_new_password, is_push_enabled, set_push_enabled,
    is_auto_send_enabled, set_auto_send,
    username_taken, contact_email_taken, phone_number_taken,
    archived_conversations_for_business, trashed_conversations_for_business,
    archive_conversation, unarchive_conversation, soft_delete_conversation, restore_conversation,
    is_reminders_enabled, set_reminders_enabled, get_upsell_pairs, set_upsell_pairs,
    email_verify_sent_recently, phone_verify_sent_recently,
)
from instagram_api import send_message as send_instagram_message, authorize_url as instagram_authorize_url
from extensions import limiter
import paddle_billing
import requests

api = Blueprint("api", __name__, url_prefix="/api")

# Current signed-APK release - bumped alongside android/app/build.gradle's versionName
# on every native release. app.py's /download route imports this too, so it's the one
# place to update.
APK_VERSION = "1.9.7"


@api.after_request
def add_cors_headers(response):
    # The mobile app's requests come from the bundled app shell's own origin, not this
    # site's - a wildcard is safe here because every route that touches account data is
    # Bearer-token authenticated (no ambient cookie), so a stray cross-site request
    # without the token still gets a 401 regardless of origin. The handful of routes that
    # are intentionally public (login, register, forgot-password, check-availability)
    # don't return or act on anything a token would have gated anyway.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# Bearer tokens are used instead of the session cookie because the mobile app's requests
# are cross-origin from the Railway domain's point of view (they come from the bundled
# app shell, not a page served by this site) - the SESSION_COOKIE_SAMESITE=Lax cookie
# wouldn't reliably be sent on those requests anyway.
TOKEN_MAX_AGE = 60 * 60 * 24 * 90  # 90 days


def _serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-api-token")


def make_token(business_id):
    return _serializer().dumps({"business_id": business_id})


def _business_id_from_token(token):
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("business_id")


# Separate short-lived signed token (distinct salt, 10-minute expiry) used ONLY to carry
# a business_id through the Instagram OAuth round-trip for the mobile app, which never
# holds a website session cookie. Passed as the OAuth "state" param - see instagram_api.py's
# authorize_url and app.py's instagram_oauth_callback.
INSTAGRAM_CONNECT_STATE_MAX_AGE = 60 * 10


def _instagram_connect_state_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-instagram-connect-state")


def make_instagram_connect_state(business_id):
    return _instagram_connect_state_serializer().dumps({"business_id": business_id})


def business_id_from_instagram_connect_state(state):
    if not state:
        return None
    try:
        data = _instagram_connect_state_serializer().loads(state, max_age=INSTAGRAM_CONNECT_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("business_id")


# Same bridge pattern as the Instagram-connect state token above, for the same reason:
# the mobile app can't attach its Bearer token to a request opened via Capacitor's
# Browser plugin, so /api/export-data-link hands out one of these short-lived signed
# tokens instead, which app.py's /export-data/<token> accepts in place of a session.
EXPORT_LINK_MAX_AGE = 60 * 10


def _export_link_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-export-data-link")


def make_export_link_token(business_id):
    return _export_link_serializer().dumps({"business_id": business_id})


def business_id_from_export_link_token(token):
    if not token:
        return None
    try:
        data = _export_link_serializer().loads(token, max_age=EXPORT_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("business_id")


# Same bridge, for the Subscription screen's "Choose a plan" button - see
# api_subscription_link above and app.py's /subscription/<token>.
SUBSCRIPTION_LINK_MAX_AGE = 60 * 10


def _subscription_link_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-subscription-link")


def make_subscription_link_token(business_id):
    return _subscription_link_serializer().dumps({"business_id": business_id})


def business_id_from_subscription_link_token(token):
    if not token:
        return None
    try:
        data = _subscription_link_serializer().loads(token, max_age=SUBSCRIPTION_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("business_id")


# Same bridge, for the mobile app's "Schedule" row (Faza 11) - the day/week calendar
# only exists on the website (a full custom calendar UI isn't worth duplicating
# natively), so this opens it in the in-app browser exactly like Subscription does.
SCHEDULE_LINK_MAX_AGE = 60 * 10


def _schedule_link_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-schedule-link")


def make_schedule_link_token(business_id):
    return _schedule_link_serializer().dumps({"business_id": business_id})


def business_id_from_schedule_link_token(token):
    if not token:
        return None
    try:
        data = _schedule_link_serializer().loads(token, max_age=SCHEDULE_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("business_id")


def require_api_auth(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify(error="unauthorized"), 401

        business_id = _business_id_from_token(auth_header[len("Bearer "):])
        if business_id is None or business_by_id(business_id) is None:
            return jsonify(error="unauthorized"), 401

        request.business_id = business_id
        return function(*args, **kwargs)
    return wrapped


def _serialize_conversation(c):
    id_, sender, subject, question, suggested_reply, final_reply, status, created_at = c
    return {
        "id": id_,
        "sender": sender,
        "subject": subject,
        "question": question,
        "suggested_reply": suggested_reply,
        "final_reply": final_reply,
        "status": status,
        "created_at": created_at.isoformat() if created_at else None,
    }


@api.route("/login", methods=["POST"])
@limiter.limit("10 per 15 minutes")
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    business = business_by_login_identifier(username)
    if not business or not check_password_hash(business[3], password):
        return jsonify(error="Wrong username or password."), 401

    if not business[12]:  # email_verified
        from app import send_verification_email, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

        if not email_verify_sent_recently(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
            send_verification_email(business[0], business[1], business[4])
        return jsonify(
            error="Please confirm your email first - we've sent a confirmation link to your inbox.",
            pending_verification=True,
            business_id=business[0],
        ), 403

    return jsonify(token=make_token(business[0]), business_id=business[0], business_name=business[1])


# Native registration screen in the mobile app (replaces opening the website's /register
# in an in-app browser tab). Mirrors app.py's web /register route's validation exactly,
# but responds with JSON instead of redirecting. Unlike before, success no longer hands
# back a token - the account can't log in until its email is confirmed (see api_login
# above), so the app shows a "check your email" screen instead of the Instagram-connect
# screen until that happens.
@api.route("/register", methods=["POST"])
@limiter.limit("5 per hour")
def api_register():
    data = request.get_json(silent=True) or {}

    if not data.get("accept_terms"):
        return jsonify(error="You must accept the Terms of Service and Privacy Policy."), 400

    if len(data.get("password", "")) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400

    try:
        new_id = add_business(
            name=data.get("name", ""),
            username=data.get("username", ""),
            password=data.get("password", ""),
            contact_email=data.get("contact_email", ""),
            working_hours=data.get("working_hours", ""),
            price_list=data.get("price_list", ""),
            faq=data.get("faq", ""),
            phone_number=data.get("phone_number") or None,
            # Same "no free access without a card once Paddle is really live" rule as
            # the web /register route (see app.py) - the app's own Subscription screen
            # already has a "needs plan" state (subscription_status == 'none') that opens
            # the same Paddle Checkout trial bridge as web, see /api/subscription-link.
            initial_status="none" if paddle_billing.is_configured() else "trialing",
        )
    except psycopg2.errors.UniqueViolation:
        return jsonify(error="That username, contact email, or phone number is already taken."), 409

    log_event("register_completed", business_id=new_id)
    from app import send_verification_email  # deferred: avoid circular import at module load

    send_verification_email(new_id, data.get("name", ""), data.get("contact_email", ""))
    return jsonify(pending_verification=True, business_id=new_id, contact_email=data.get("contact_email", ""))


# Lets the app's "check your email" screen (shown after registration, or after a login
# attempt against an unconfirmed account) trigger a resend without the user re-typing
# their password - business_id alone is enough since this only ever resends to the email
# already on file, never exposes or changes anything. Rate-limited (and further
# throttled by email_verify_sent_recently) since it's intentionally unauthenticated.
@api.route("/resend-verification", methods=["POST"])
@limiter.limit("5 per hour")
def api_resend_verification():
    from app import send_verification_email, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

    data = request.get_json(silent=True) or {}
    business_id = data.get("business_id")
    business = business_by_id(business_id) if business_id else None

    if business and not business[12] and not email_verify_sent_recently(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
        send_verification_email(business[0], business[1], business[4])

    # Same response whether or not business_id was real/already-verified - nothing here
    # should let a caller probe for valid account ids.
    return jsonify(sent=True)


# Native forgot-password screen in the mobile app (replaces opening the website's
# /forgot-password in an in-app browser tab). Reuses app.py's request_password_reset
# helper (deferred import - avoids a circular import at module load) so the actual
# lookup/email logic isn't duplicated between the web route and this one. Accepts either
# an email or a username (field is labeled "Email or username" on the screen).
@api.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per hour")
def api_forgot_password():
    from app import request_password_reset

    data = request.get_json(silent=True) or {}
    if not request_password_reset(data.get("identifier", "")):
        return jsonify(error="No account found with that email or username."), 404
    return jsonify(message="We've sent a password reset link to the email on file.")


# "Find by mobile number" - same idea as /api/forgot-password above, but looks the
# account up by its registered phone number instead. See app.py's
# request_password_reset_by_phone for why the actual link still only ever goes out by email.
@api.route("/forgot-password/phone", methods=["POST"])
@limiter.limit("5 per hour")
def api_forgot_password_phone():
    from app import request_password_reset_by_phone

    data = request.get_json(silent=True) or {}
    if not request_password_reset_by_phone(data.get("phone_number", "")):
        return jsonify(error="No account found with that phone number."), 404
    return jsonify(message="We found your account - we've sent a password reset link to the email on file.")


# Behind both the Create Account screen (no auth yet) and the Profile screen (Bearer-
# authed) - lets the app show an inline "already taken" error as the user types, instead
# of only finding out from a 409 after submitting. Deliberately NOT behind
# @require_api_auth since Create Account has no token yet; when a valid Bearer token IS
# present (Profile), the caller's own current value is excluded so editing a field
# without changing it never flags itself as taken. Same underlying checks as the web
# /check-availability in app.py.
_AVAILABILITY_CHECKS = {
    "username": username_taken,
    "contact_email": contact_email_taken,
    "phone_number": phone_number_taken,
}


@api.route("/check-availability", methods=["POST"])
@limiter.limit("60 per minute")
def api_check_availability():
    data = request.get_json(silent=True) or {}
    value = (data.get("value") or "").strip()
    check = _AVAILABILITY_CHECKS.get(data.get("field"))
    if check is None:
        return jsonify(error="invalid field"), 400
    if not value:
        return jsonify(taken=False)

    exclude_id = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        exclude_id = _business_id_from_token(auth_header[len("Bearer "):])

    return jsonify(taken=check(value, exclude_business_id=exclude_id))


# Returns the Instagram OAuth authorize URL for the mobile app to open in its in-app
# browser - the app can't use the website's session-cookie-based /connect-instagram/start
# (it never has a session cookie, only this endpoint's Bearer token), so business identity
# is carried through the OAuth round-trip via the "state" param instead. See
# make_instagram_connect_state and app.py's instagram_oauth_callback.
@api.route("/instagram-connect-url")
@require_api_auth
def api_instagram_connect_url():
    redirect_uri = url_for("instagram_oauth_callback", _external=True)
    state = make_instagram_connect_state(request.business_id)
    return jsonify(url=instagram_authorize_url(redirect_uri, state=state))


# Returns a short-lived link the mobile app opens via its in-app browser (same mechanism
# as the "Download update" flow) to trigger a downloadable JSON export of the business's
# own data - see make_export_link_token and app.py's /export-data/<token>.
@api.route("/export-data-link")
@require_api_auth
def api_export_data_link():
    token = make_export_link_token(request.business_id)
    return jsonify(url=f"{request.url_root}export-data/{token}")


# Same bridge pattern as the two above, for the Subscription screen's "Choose a plan"
# button (Faza 5) - the full Starter/Pro pricing UI (Monthly/Yearly toggle, Paddle
# checkout) only exists on the website, not duplicated natively. See
# make_subscription_link_token and app.py's /subscription/<token>.
@api.route("/subscription-link")
@require_api_auth
def api_subscription_link():
    token = make_subscription_link_token(request.business_id)
    return jsonify(url=f"{request.url_root}subscription/{token}")


# Same bridge pattern, for the Schedule row (Faza 11) - see make_schedule_link_token and
# app.py's /schedule/<token>.
@api.route("/schedule-link")
@require_api_auth
def api_schedule_link():
    token = make_schedule_link_token(request.business_id)
    return jsonify(url=f"{request.url_root}schedule/{token}")


@api.route("/dashboard")
@require_api_auth
def api_dashboard():
    from app import DAILY_REPLY_LIMIT, BETA_FREE_MODE  # deferred to avoid a circular import at module load

    business_id = request.business_id
    business = business_by_id(business_id)
    conversations = conversations_for_business(business_id)
    # Archived/Trash counts and rows are fetched every call (not just when that tab is
    # selected) so their filter-pill badges stay accurate no matter which tab is active -
    # same reasoning as pending_count/sent_count already being computed unconditionally.
    archived = archived_conversations_for_business(business_id)
    trashed = trashed_conversations_for_business(business_id)

    filter_status = request.args.get("filter")
    if filter_status == "archived":
        shown = archived
    elif filter_status == "trash":
        shown = trashed
    elif filter_status in ("pending", "sent"):
        shown = [c for c in conversations if c[6] == filter_status]
    else:
        filter_status = "all"
        shown = conversations

    pending_count = sum(1 for c in conversations if c[6] == "pending")

    return jsonify(
        business_name=business[1],
        filter=filter_status,
        conversations=[_serialize_conversation(c) for c in shown],
        total_count=len(conversations),
        pending_count=pending_count,
        sent_count=len(conversations) - pending_count,
        archived_count=len(archived),
        trash_count=len(trashed),
        today_processed=replies_today_count(business_id),
        daily_limit=DAILY_REPLY_LIMIT,
        subscription_active=effective_subscription_active(business_id),
        subscription_status=get_subscription_status(business_id),
        pro_access=BETA_FREE_MODE or has_pro_access(business_id),
        beta_free_mode=BETA_FREE_MODE,
        stats=business_stats(business_id),
        # The app only ever calls PushNotifications.register() when this is true - avoids
        # touching that native/Firebase code path at all until a Firebase project actually
        # exists (FIREBASE_PROJECT_ID + FIREBASE_SERVICE_ACCOUNT_JSON are set on the server).
        push_enabled=bool(os.getenv("FIREBASE_PROJECT_ID")),
    )


def _owned_conversation(conversation_id):
    """Shared ownership check for the archive/delete/restore endpoints below - returns
    (conversation, None) on success or (None, (json, status)) on failure, mirroring
    api_send_reply's existing 404-vs-403 distinction (guessable IDs shouldn't let one
    business act on another's conversations)."""
    conversation = get_conversation(conversation_id)
    if conversation is None:
        return None, (jsonify(error="not found"), 404)
    if conversation[1] != request.business_id:
        return None, (jsonify(error="forbidden"), 403)
    return conversation, None


@api.route("/conversations/<int:conversation_id>/archive", methods=["POST"])
@require_api_auth
def api_archive_conversation(conversation_id):
    _, error = _owned_conversation(conversation_id)
    if error:
        return error
    archive_conversation(conversation_id)
    return jsonify(message="archived")


@api.route("/conversations/<int:conversation_id>/unarchive", methods=["POST"])
@require_api_auth
def api_unarchive_conversation(conversation_id):
    _, error = _owned_conversation(conversation_id)
    if error:
        return error
    unarchive_conversation(conversation_id)
    return jsonify(message="unarchived")


@api.route("/conversations/<int:conversation_id>/delete", methods=["POST"])
@require_api_auth
def api_delete_conversation(conversation_id):
    _, error = _owned_conversation(conversation_id)
    if error:
        return error
    soft_delete_conversation(conversation_id)
    return jsonify(message="deleted")


@api.route("/conversations/<int:conversation_id>/restore", methods=["POST"])
@require_api_auth
def api_restore_conversation(conversation_id):
    _, error = _owned_conversation(conversation_id)
    if error:
        return error
    restore_conversation(conversation_id)
    return jsonify(message="restored")


@api.route("/conversations/<int:conversation_id>/send", methods=["POST"])
@require_api_auth
def api_send_reply(conversation_id):
    data = request.get_json(silent=True) or {}
    final_reply = data.get("reply", "")

    conversation = get_conversation(conversation_id)
    if conversation is None:
        return jsonify(error="not found"), 404

    _, business_id, sender, subject, question, suggested_reply, sender_igsid = conversation
    if business_id != request.business_id:
        return jsonify(error="forbidden"), 403

    business = business_by_id(business_id)
    # indices: 5 instagram_account_id, 7 instagram_access_token
    debug_context = f"reply-send-debug conversation_id={conversation_id} sender_igsid={sender_igsid} ig_account_id={business[5]}"
    try:
        result = send_instagram_message(sender_igsid, final_reply, business[7], business[5])
        # Temporary diagnostic: the dashboard/app was showing "sent" (Instagram's API
        # accepted the request with 2xx) while the reply never actually reached the
        # customer's real Instagram DM thread - logging Instagram's own response body
        # here (viewable at /admin/client-errors) so we can see exactly what it returned
        # (message id, recipient id echoed back, etc.) instead of guessing blind.
        log_client_error(business_id, f"instagram send OK: {result}", "", debug_context, "")
    except requests.RequestException as e:
        error_body = e.response.text if getattr(e, "response", None) is not None else str(e)
        log_client_error(business_id, f"instagram send FAILED: {error_body}", "", debug_context, "")
        # Most likely Instagram's 24-hour reply window has passed - an expected business
        # outcome, not a bug, hence the specific message rather than a generic 500.
        return jsonify(error="Couldn't send - Instagram only allows replying within 24 hours of the customer's last message."), 400

    mark_sent(conversation_id, final_reply)

    if business_stats(business_id)["total_sent"] == 1:
        log_event("first_reply_sent", business_id=business_id)

    return jsonify(message="sent")


@api.route("/settings", methods=["GET"])
@require_api_auth
def api_get_settings():
    business = business_by_id(request.business_id)
    return jsonify(
        name=business[1],
        username=business[2],
        contact_email=business[4],
        instagram_username=business[6],
        working_hours=business[8],
        price_list=business[9],
        faq=business[10],
        phone_number=business[11] or "",
        phone_verified=business[13],
        upsell_pairs=get_upsell_pairs(request.business_id) or "",
    )


@api.route("/settings", methods=["POST"])
@require_api_auth
def api_update_settings():
    from app import BETA_FREE_MODE  # deferred to avoid a circular import at module load

    data = request.get_json(silent=True) or {}
    business_id = request.business_id
    business = business_by_id(business_id)

    name = data.get("name", business[1])
    username = data.get("username", business[2]).strip()
    contact_email = data.get("contact_email", business[4]).strip()
    phone_number = data.get("phone_number", business[11] or "").strip()
    working_hours = data.get("working_hours", business[8])
    price_list = data.get("price_list", business[9])
    faq = data.get("faq", business[10])

    if not username:
        return jsonify(error="Username is required."), 400
    if not contact_email:
        return jsonify(error="Contact email is required."), 400

    new_password = data.get("new_password") or None
    if new_password:
        if not check_password_hash(business[3], data.get("current_password", "")):
            return jsonify(error="Current password is incorrect."), 400
        if len(new_password) < 8:
            return jsonify(error="New password must be at least 8 characters."), 400

    try:
        update_business(business_id, name, username, contact_email, phone_number or None, working_hours, price_list, faq)
    except psycopg2.errors.UniqueViolation:
        return jsonify(error="That username, contact email, or phone number is already taken."), 409

    # Mirrors update_business()'s own CASE-on-old-value logic (index 11 phone_number, 13
    # phone_verified from the business row fetched above, before this update) instead of
    # an extra round-trip to re-fetch it.
    phone_verified = business[13] and phone_number == (business[11] or "")

    if new_password:
        set_new_password(business_id, new_password)

    # AI upsell pairs (Faza 9/11) are Pro-only - same server-side gate as the website's
    # /settings route, so a Starter-plan app user can't save one either. Silently ignored
    # (not saved) rather than erroring if sent anyway, same "the UI already hides this,
    # a raw request just gets no-oped" spirit as the reminders toggle above.
    upsell_pairs = get_upsell_pairs(business_id) or ""
    if "upsell_pairs" in data and (BETA_FREE_MODE or has_pro_access(business_id)):
        upsell_pairs = data["upsell_pairs"]
        set_upsell_pairs(business_id, upsell_pairs)

    return jsonify(
        message="Saved.", name=name, username=username, contact_email=contact_email,
        phone_number=phone_number, phone_verified=phone_verified,
        working_hours=working_hours, price_list=price_list, faq=faq,
        upsell_pairs=upsell_pairs,
    )


# Triggered by the "Verify phone number" button on the Profile screen - unlike email
# verification this is never sent automatically (phone_number is optional and doesn't
# gate anything on its own), only on request. Sends to the account's own contact email,
# same as everywhere else phone verification is confirmed - there's no SMS integration.
@api.route("/verify-phone/send", methods=["POST"])
@require_api_auth
@limiter.limit("5 per hour")
def api_send_phone_verification():
    from app import send_phone_verification_email, PHONE_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

    business = business_by_id(request.business_id)
    if not business[11]:
        return jsonify(error="No phone number on file yet - add one first."), 400
    if business[13]:
        return jsonify(error="This phone number is already verified."), 400

    if not phone_verify_sent_recently(business[0], PHONE_VERIFY_RESEND_COOLDOWN_SECONDS):
        send_phone_verification_email(business[0], business[1], business[11], business[4])
    return jsonify(sent=True)


@api.route("/push-token", methods=["POST"])
@require_api_auth
def api_save_push_token():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="missing token"), 400
    save_push_token(request.business_id, token)
    return jsonify(message="saved")


# Instant-apply preferences shown in the app's Settings tab (as opposed to Profile's
# bulk "Save changes" form) - each one is its own small GET/POST pair, same pattern as
# language/theme (except those are local-only, these need the server to actually know).
@api.route("/notification-preferences", methods=["GET"])
@require_api_auth
def api_get_notification_preferences():
    return jsonify(
        push_enabled=is_push_enabled(request.business_id),
        auto_send=is_auto_send_enabled(request.business_id),
        reminders_enabled=is_reminders_enabled(request.business_id),
    )


@api.route("/notification-preferences", methods=["POST"])
@require_api_auth
def api_update_notification_preferences():
    from app import BETA_FREE_MODE  # deferred to avoid a circular import at module load

    data = request.get_json(silent=True) or {}
    if "push_enabled" in data:
        set_push_enabled(request.business_id, bool(data["push_enabled"]))
    if "auto_send" in data:
        set_auto_send(request.business_id, bool(data["auto_send"]))
    # No-show reminders (Faza 9/11) are Pro-only - same server-side gate as the website's
    # /app-settings/reminders, so a Starter-plan app user can't flip this on either.
    if "reminders_enabled" in data and (BETA_FREE_MODE or has_pro_access(request.business_id)):
        set_reminders_enabled(request.business_id, bool(data["reminders_enabled"]))
    return jsonify(
        message="saved",
        push_enabled=is_push_enabled(request.business_id),
        auto_send=is_auto_send_enabled(request.business_id),
        reminders_enabled=is_reminders_enabled(request.business_id),
    )


# Mobile equivalent of the web dashboard's "Delete account" button - same underlying
# delete_business(), just Bearer-authed instead of session-based.
@api.route("/delete-account", methods=["POST"])
@require_api_auth
def api_delete_account():
    delete_business(request.business_id)
    return jsonify(message="deleted")


# Unauthenticated on purpose - a crash can happen before login (or because the auth
# token itself is broken), and this must still get through. Field lengths are capped
# to keep the table from growing unbounded from a misbehaving/malicious client.
@api.route("/client-error", methods=["POST"])
def api_client_error():
    data = request.get_json(silent=True) or {}

    business_id = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        business_id = _business_id_from_token(auth_header[len("Bearer "):])

    log_client_error(
        business_id,
        (data.get("message") or "")[:2000],
        (data.get("stack") or "")[:4000],
        (data.get("context") or "")[:500],
        (data.get("app_version") or "")[:40],
    )
    return jsonify(message="logged")


# Self-hosted OTA update manifest for the Capacitor app (capacitor-updater plugin) -
# points the app at the latest mobile/ web-assets bundle without needing a new signed
# APK build for every small change (see build_mobile_bundle.py).
#
# The version string is a hash of the bundle's own content, not a hand-picked label.
# Originally this was a MOBILE_BUNDLE_VERSION Railway env var, bumped by hand on every
# release - except changing a Railway env var needs the Railway CLI, which is blocked on
# this machine (see deploy-railway-state memory), so in practice the same version string
# ("1.9.0") got reused across several actually-different bundle updates. That's a real bug:
# the app compares the manifest's version against what it last stored, so once a device
# had successfully applied "1.9.0" once, every later push under that same unchanged label
# was silently skipped as "already applied", even though the zip's contents kept changing.
# Hashing the file removes the whole class of mistake - the version now changes
# automatically, exactly when and only when the content actually does.
_MOBILE_BUNDLE_PATH = os.path.join(os.path.dirname(__file__), "static", "mobile-bundle.zip")


@api.route("/mobile-manifest")
def api_mobile_manifest():
    if not os.path.exists(_MOBILE_BUNDLE_PATH):
        return jsonify(available=False)
    with open(_MOBILE_BUNDLE_PATH, "rb") as f:
        version = hashlib.sha256(f.read()).hexdigest()[:16]
    return jsonify(
        available=True,
        version=version,
        url=f"{request.url_root}static/mobile-bundle.zip",
    )


# Native-code changes (new plugin, permissions, icon, splash, anything under android/)
# can't ship through the OTA mechanism above - it only swaps the JS/HTML/CSS bundle.
# The app polls this instead to offer an in-app "new version available" download
# prompt for those, rather than requiring the user to be sent a link manually.
@api.route("/apk-version")
def api_apk_version():
    return jsonify(
        version=APK_VERSION,
        url=f"{request.url_root}download/camilius-ai.apk",
    )
