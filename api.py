import os
import secrets
from datetime import date as date_type, datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_login_identifier,
    business_by_id, business_by_contact_email, replies_today_count, update_business, get_subscription_status,
    effective_subscription_active, has_pro_access, business_stats, log_event, save_push_token, log_client_error,
    add_business, delete_business, set_new_password, is_push_enabled, set_push_enabled,
    contact_email_taken, phone_number_taken,
    archived_conversations_for_business, trashed_conversations_for_business,
    archive_conversation, unarchive_conversation, soft_delete_conversation, restore_conversation,
    get_upsell_pairs, set_upsell_pairs,
    is_owner_reminders_enabled, set_owner_reminders_enabled, get_owner_reminder_minutes, set_owner_reminder_minutes,
    email_verify_sent_recently, phone_verify_sent_recently,
    email_verify_resend_wait_seconds, phone_verify_resend_wait_seconds, mark_phone_verified,
    business_email_verify_code_matches, mark_email_verified,
    business_phone_verify_code_matches,
    get_slot_duration, set_slot_duration, create_appointment, get_appointment, cancel_appointment,
    notifications_for_business, unread_notification_count, mark_notifications_read,
    is_new_booking_notifications_enabled, set_new_booking_notifications_enabled,
    record_account_deletion, ACCOUNT_DELETION_REASONS,
    create_oauth_login_session, oauth_login_session_status,
    get_business_page, upsert_business_page, page_by_slug, appointments_in_range,
    set_preferred_theme,
    UniqueViolation,
)
from extensions import limiter
from send_sms import sms_configured
import booking
import paddle_billing
import google_oauth
import business_media
import business_documents
import pages
import themes
import ai_theme
import domain_lookup
from page_generator import generate_page_draft, format_working_hours, format_price_list, format_faq, transcribe_voice_description, MAX_VOICE_AUDIO_BYTES

api = Blueprint("api", __name__, url_prefix="/api")

# Current signed-APK release - bumped alongside android/app/build.gradle's versionName
# on every native release. app.py's /download route imports this too, so it's the one
# place to update.
APK_VERSION = "1.9.15"


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
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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


# Short-lived signed token (10-minute expiry) used to bridge a business_id across a
# request the mobile app opens in Capacitor's Browser plugin, which can't attach its
# Bearer token to that request - so /api/export-data-link hands out one of these tokens
# instead, which app.py's /export-data/<token> accepts in place of a session.
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


# Same bridge again, for the "Buy this domain" checkout (see api_domain_checkout_link
# below and app.py's /domain-checkout/<token>) - carries the specific domain too (not
# just business_id), since which domain the checkout page shows/charges for has to
# come from somewhere the client can't just override by editing a request body.
DOMAIN_CHECKOUT_LINK_MAX_AGE = 60 * 10


def _domain_checkout_link_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-domain-checkout-link")


def make_domain_checkout_link_token(business_id, domain):
    return _domain_checkout_link_serializer().dumps({"business_id": business_id, "domain": domain})


def domain_checkout_link_token_data(token):
    if not token:
        return None, None
    try:
        data = _domain_checkout_link_serializer().loads(token, max_age=DOMAIN_CHECKOUT_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None, None
    return data.get("business_id"), data.get("domain")


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


# Same bridge, for the mobile app's "View page" button (My Site home screen) - the full
# public_page.html preview render only exists on the website, so this opens it in the
# in-app browser exactly like Schedule/Subscription do above.
SITE_PREVIEW_LINK_MAX_AGE = 60 * 10


def _site_preview_link_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="mobile-site-preview-link")


def make_site_preview_link_token(business_id):
    return _site_preview_link_serializer().dumps({"business_id": business_id})


def business_id_from_site_preview_link_token(token):
    if not token:
        return None
    try:
        data = _site_preview_link_serializer().loads(token, max_age=SITE_PREVIEW_LINK_MAX_AGE)
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
        business = business_by_id(business_id) if business_id is not None else None
        if business is None:
            return jsonify(error="unauthorized"), 401

        request.business_id = business_id
        # Stashed so handlers that need the row (e.g. api_schedule) don't have to pay for
        # a second business_by_id round trip right after this one.
        request.business = business
        return function(*args, **kwargs)
    return wrapped


def _serialize_conversation(c):
    id_, sender, subject, question, suggested_reply, final_reply, status, created_at, profile_pic_url, expired_at, channel = c
    return {
        "id": id_,
        "sender": sender,
        "subject": subject,
        "question": question,
        "suggested_reply": suggested_reply,
        "final_reply": final_reply,
        "status": status,
        "created_at": created_at.isoformat() if created_at else None,
        "profile_pic_url": profile_pic_url,
        "expired": expired_at is not None,
        "channel": channel,
    }


@api.route("/login", methods=["POST"])
@limiter.limit("10 per 15 minutes")
def api_login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "")
    password = data.get("password", "")

    business = business_by_login_identifier(email)
    if not business or not check_password_hash(business[2], password):
        return jsonify(error="Wrong email or password."), 401

    if not business[11]:  # email_verified
        from app import send_verification_email, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

        if not email_verify_sent_recently(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
            send_verification_email(business[0], business[1], business[3])
        return jsonify(
            error="Please confirm your email first - we've sent a confirmation link to your inbox.",
            pending_verification=True,
            business_id=business[0],
            wait_seconds=email_verify_resend_wait_seconds(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS),
        ), 403

    return jsonify(token=make_token(business[0]), business_id=business[0], business_name=business[1])


# Native registration screen in the mobile app (replaces opening the website's /register
# in an in-app browser tab). Mirrors app.py's web /register route's validation exactly,
# but responds with JSON instead of redirecting. Unlike before, success no longer hands
# back a token - the account can't log in until its email is confirmed (see api_login
# above), so the app shows a "check your email" screen until that happens.
@api.route("/register", methods=["POST"])
@limiter.limit("5 per hour")
def api_register():
    data = request.get_json(silent=True) or {}

    if not data.get("accept_terms"):
        return jsonify(error="You must accept the Terms of Service and Privacy Policy."), 400

    if len(data.get("password", "")) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400

    from app import send_verification_email, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

    # Someone who abandoned signup before confirming their email has nothing but a dead
    # end otherwise (unverified, so can't log in; nothing wrong with the password, so
    # "Forgot password" doesn't help either) - mirrors app.py's web register() route.
    # Resume the existing unverified account instead of blocking a second attempt.
    existing = business_by_contact_email(data.get("contact_email", ""))
    if existing and not existing[11]:  # email_verified
        if not email_verify_sent_recently(existing[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
            send_verification_email(existing[0], existing[1], existing[3])
        return jsonify(
            pending_verification=True,
            business_id=existing[0],
            contact_email=existing[3],
            wait_seconds=email_verify_resend_wait_seconds(existing[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS),
        )

    # "Continue with Google" prefilled the sign-up screen (see api_google_login_status
    # below) rather than creating an account outright - google_link_token carries google_id
    # back through, re-verified here (never trusted from the client alone) and only
    # honored if the email it was issued for still matches what was actually submitted. A
    # visitor who changed the email field after Google prefilled it just falls back to a
    # normal, unverified signup - exactly as if they'd never used Google at all.
    google_link = google_oauth.verify_link_token(data.get("google_link_token", ""))
    google_id = None
    if google_link and google_link.get("email") == (data.get("contact_email") or "").strip().lower():
        google_id = google_link.get("google_id")

    try:
        new_id = add_business(
            name=data.get("name", ""),
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
            # The registration wizard's own "Enable notifications?" toggle - whatever the
            # user picked there is the account's starting push_enabled value, changeable
            # later from Settings (see /api/notification-preferences) either way.
            push_enabled=bool(data.get("push_enabled", True)),
            new_booking_notifications_enabled=bool(data.get("new_booking_notifications_enabled", True)),
            # Unlike the two above, defaults to False (matching the column's own
            # default) and isn't Pro-gated here the way Settings' own save handler gates
            # it (state.proAccess ? checked : undefined) - a fresh signup's pro status
            # isn't known client-side yet, and reminders.py's actual send path already
            # checks has_pro_access() independently before ever looking at this flag, so
            # storing True for a non-pro account is inert rather than a real leak.
            reminders_enabled=bool(data.get("reminders_enabled", False)),
            google_id=google_id,
            email_verified=bool(google_id),
        )
    except UniqueViolation:
        return jsonify(error="That contact email or phone number is already taken."), 409

    log_event("register_completed", business_id=new_id)

    if google_id:
        # Google already proved this email - no confirmation link needed, hand back a
        # usable Bearer token straight away exactly like /api/login does.
        return jsonify(token=make_token(new_id), business_id=new_id, business_name=data.get("name", ""))

    send_verification_email(new_id, data.get("name", ""), data.get("contact_email", ""))
    return jsonify(
        pending_verification=True,
        business_id=new_id,
        contact_email=data.get("contact_email", ""),
        wait_seconds=EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS,
    )


# "Continue with Google" (mobile). The app has no cookie session in the system browser
# tab Capacitor's Browser plugin opens Google's consent screen in (see
# mobile/app.js's startGoogleLogin), so unlike the web flow (app.py's auth_google(), which
# just redirects straight to Google) this hands the app a ready-made auth_url to open
# itself, plus a `state` token it polls api_google_login_status below with until
# app.py's /auth/google/callback has filled the matching oauth_login_sessions row in.
@api.route("/google-login/start", methods=["POST"])
@limiter.limit("10 per 15 minutes")
def api_google_login_start():
    if not google_oauth.is_configured():
        return jsonify(error="Google sign-in isn't available."), 404

    data = request.get_json(silent=True) or {}
    # Which screen the app's "Continue with Google" button lives on - 'login' (Login
    # screen) or 'register' (Create Account screen), mirrored back to us by
    # /auth/google/callback via oauth_login_session_intent(). Anything else falls back to
    # 'register', same as omitting it entirely - the pre-existing "hand new identities to
    # sign-up" behavior.
    intent = "login" if data.get("intent") == "login" else "register"

    state = secrets.token_urlsafe(24)
    create_oauth_login_session(state, intent)
    redirect_uri = url_for("auth_google_callback", _external=True)
    return jsonify(auth_url=google_oauth.authorization_url(redirect_uri, state), state=state)


# Polled every GOOGLE_LOGIN_POLL_INTERVAL_MS (see mobile/app.js's pollGoogleLogin) while
# its system-browser tab is open - 'pending' until /auth/google/callback resolves the
# attempt one way or the other. A 'done'/'error' result (or the row simply not existing -
# either never created, or already consumed by an earlier poll) is single-use, matching
# database.oauth_login_session_status's own docstring. `state` is a 24-byte
# secrets.token_urlsafe value - too high-entropy to usefully brute-force within this
# window even at this rate, so the limit here is really just a sanity cap, not real
# abuse resistance. Sized comfortably above one attempt's own worst case (120s timeout /
# 400ms interval = 300 requests) so a single slow sign-in never trips it on its own.
@api.route("/google-login/status", methods=["GET"])
@limiter.limit("400 per 5 minutes")
def api_google_login_status():
    state = request.args.get("state", "")
    result = oauth_login_session_status(state) if state else None
    if result is None:
        return jsonify(status="expired")
    return jsonify(**result)


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

    if business and not business[11] and not email_verify_sent_recently(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
        send_verification_email(business[0], business[1], business[3])

    # Same response whether or not business_id was real/already-verified - nothing here
    # should let a caller probe for valid account ids. A real business always reports the
    # actual remaining cooldown (freshly 60s after the send above, or whatever's left of an
    # earlier one if it was throttled); an invalid/verified id just gets the full cooldown
    # back, indistinguishable from a real send just having happened.
    wait_seconds = (
        email_verify_resend_wait_seconds(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS)
        if business and not business[11]
        else EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS
    )
    return jsonify(sent=True, wait_seconds=wait_seconds)


# Redeems the code emailed by send_verification_email() from the app's own "check your
# email" screen (showVerifyPendingScreen in mobile/app.js) - the in-app counterpart to
# app.py's web /verify-pending/confirm. Unlike the web flow (which can only mark the
# session that's already sitting on verify_pending as logged-in), this hands back a
# Bearer token on success so the code entered here is the whole continuation: the app
# never has to fall back to opening anything in a browser to finish signing in.
@api.route("/verify-email-code", methods=["POST"])
@limiter.limit("10 per 15 minutes")
def api_verify_email_code():
    data = request.get_json(silent=True) or {}
    business_id = data.get("business_id")
    code = (data.get("code") or "").strip()

    business = business_by_id(business_id) if business_id else None
    if business is None or business[11]:  # unknown account, or already verified - nothing to confirm
        return jsonify(error="Incorrect or expired code. Please try again."), 400

    if not business_email_verify_code_matches(business[0], code):
        return jsonify(error="Incorrect or expired code. Please try again."), 400

    mark_email_verified(business[0])
    log_event("email_verified", business_id=business[0])
    return jsonify(token=make_token(business[0]), business_id=business[0], business_name=business[1])


# Native forgot-password screen in the mobile app (replaces opening the website's
# /forgot-password in an in-app browser tab). Reuses app.py's request_password_reset
# helper (deferred import - avoids a circular import at module load) so the actual
# lookup/email logic isn't duplicated between the web route and this one.
@api.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per hour")
def api_forgot_password():
    from app import request_password_reset

    data = request.get_json(silent=True) or {}
    if not request_password_reset(data.get("identifier", "")):
        return jsonify(error="No account found with that email."), 404
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
    "contact_email": contact_email_taken,
    "phone_number": phone_number_taken,
}


@api.route("/check-availability", methods=["POST"])
@limiter.limit("60 per minute")
def api_check_availability():
    data = request.get_json(silent=True) or {}
    value = (data.get("value") or "").strip()
    field = data.get("field")
    check = _AVAILABILITY_CHECKS.get(field)
    if check is None:
        return jsonify(error="invalid field"), 400
    if not value:
        return jsonify(taken=False)

    exclude_id = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        exclude_id = _business_id_from_token(auth_header[len("Bearer "):])

    if field == "contact_email" and exclude_id is None:
        # Unauthenticated caller, i.e. the Create Account wizard (Profile's email field
        # always sends a Bearer token - see the branch below) - same "an abandoned,
        # never-verified signup is resumable, not a real conflict" exception as the web
        # /check-availability route (see app.py), so the wizard's Next step doesn't stay
        # blocked on an email api_register() is about to let the user resume anyway.
        existing = business_by_contact_email(value)
        return jsonify(taken=bool(existing and existing[11]))

    return jsonify(taken=check(value, exclude_business_id=exclude_id))


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


# Same bridge pattern, for the "Buy this domain" checkout the My Site tab opens once
# api_save_page's response comes back with domain_checkout_required (Faza "domen plaća
# korisnik") - the domain itself only ever exists on the website (see
# templates/domain_checkout.html), not duplicated natively. See
# make_domain_checkout_link_token and app.py's /domain-checkout/<token>.
@api.route("/domain-checkout-link")
@require_api_auth
def api_domain_checkout_link():
    # Re-validated against this business's own actual pending pick (never trust the
    # query param alone) - a client can't mint a checkout link for a domain it never
    # actually chose (or one that belongs to a different business) just by passing a
    # different ?domain= value.
    domain = domain_lookup.normalize_pending_domain(request.args.get("domain"))
    page_row = get_business_page(request.business_id)
    if not domain or not page_row or page_row[11] != domain:
        return jsonify(error="No matching domain pick to check out."), 400
    token = make_domain_checkout_link_token(request.business_id, domain)
    return jsonify(url=f"{request.url_root}domain-checkout/{token}")


# Same bridge pattern, for the Schedule row (Faza 11) - see make_schedule_link_token and
# app.py's /schedule/<token>.
@api.route("/schedule-link")
@require_api_auth
def api_schedule_link():
    token = make_schedule_link_token(request.business_id)
    return jsonify(url=f"{request.url_root}schedule/{token}")


# Same bridge pattern, for the "View page" button on the mobile app's My Site home
# screen - see make_site_preview_link_token and app.py's /site/preview/<token>.
@api.route("/site-preview-link")
@require_api_auth
def api_site_preview_link():
    token = make_site_preview_link_token(request.business_id)
    return jsonify(url=f"{request.url_root}site/preview/{token}")


# Native Schedule tab (mobile day/week calendar) - JSON equivalent of app.py's /schedule
# route, same locking/slot logic via booking.py, just serialized instead of rendered.
@api.route("/schedule")
@require_api_auth
def api_schedule():
    from app import BETA_FREE_MODE, SLOT_DURATION_CHOICES, SLOT_DURATION_MIN, SLOT_DURATION_MAX  # deferred to avoid a circular import at module load

    business_id = request.business_id
    if not (BETA_FREE_MODE or has_pro_access(business_id)):
        return jsonify(locked=True)

    business = request.business
    working_hours = business[7]
    tz = booking.tz_for(business)
    slot_minutes = get_slot_duration(business_id)
    today = booking.now_local(tz).date()

    view = request.args.get("view", "day")
    if view not in ("day", "week"):
        view = "day"
    try:
        selected_date = date_type.fromisoformat(request.args.get("date", ""))
    except ValueError:
        selected_date = today

    result = dict(
        locked=False,
        view=view,
        selected_date=selected_date.isoformat(),
        today=today.isoformat(),
        slot_minutes=slot_minutes,
        slot_duration_choices=list(SLOT_DURATION_CHOICES),
        slot_duration_min=SLOT_DURATION_MIN,
        slot_duration_max=SLOT_DURATION_MAX,
        has_any_open_day=any(booking.parse_working_hours(working_hours).values()),
    )

    if view == "week":
        week_start = selected_date - timedelta(days=selected_date.weekday())
        appts_by_day = booking.appointments_by_local_day(business_id, week_start, week_start + timedelta(days=7), tz)
        days = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            appts = sorted(appts_by_day.get(d, []), key=lambda a: a["start_local"])
            days.append({
                "date": d.isoformat(),
                "is_today": d == today,
                "closed": not booking.is_open_on(working_hours, d),
                "appointments": [
                    {"id": a["id"], "time": a["start_local"].strftime("%H:%M"), "customer_name": a["customer_name"], "source": a["source"]}
                    for a in appts
                ],
            })
        result["days"] = days
        result["prev_date"] = (week_start - timedelta(days=7)).isoformat()
        result["next_date"] = (week_start + timedelta(days=7)).isoformat()
    else:
        slots = booking.day_slots(working_hours, slot_minutes, selected_date, tz)
        day_appointments = booking.appointments_for_local_day(business_id, selected_date, tz)
        appts_by_start = {a["start_local"].strftime("%H:%M"): a for a in day_appointments}
        rows = []
        for s in slots:
            key = s.strftime("%H:%M")
            appt = appts_by_start.get(key)
            # An appointment booked with a custom duration can run longer than one grid
            # slot - the slots it swallows besides its own start need to show as
            # unavailable too, not a free "Book" button, even though no appointment
            # actually starts at their exact time. Mirrors app.py's /schedule route.
            covered_by = None
            if appt is None:
                covered_by = next((a for a in day_appointments if a["start_local"] <= s < a["end_local"]), None)
            rows.append({
                "time": key,
                "end_time": (s + timedelta(minutes=slot_minutes)).strftime("%H:%M"),
                "appointment": {
                    "id": appt["id"],
                    "customer_name": appt["customer_name"],
                    "service": appt["service"],
                    "source": appt["source"],
                    "start_time": appt["start_local"].strftime("%H:%M"),
                    "end_time": appt["end_local"].strftime("%H:%M"),
                } if appt else None,
                "covered_by": {
                    "customer_name": covered_by["customer_name"],
                    "service": covered_by["service"],
                } if covered_by else None,
                "is_past": s < booking.now_local(tz),
            })
        result["slot_rows"] = rows
        result["prev_date"] = (selected_date - timedelta(days=1)).isoformat()
        result["next_date"] = (selected_date + timedelta(days=1)).isoformat()

    return jsonify(**result)


@api.route("/schedule/appointments", methods=["POST"])
@require_api_auth
@limiter.limit("30 per minute")
def api_create_appointment():
    from app import BETA_FREE_MODE, SLOT_DURATION_MIN, SLOT_DURATION_MAX  # deferred to avoid a circular import at module load

    business_id = request.business_id
    if not (BETA_FREE_MODE or has_pro_access(business_id)):
        return jsonify(error="forbidden"), 403

    business = business_by_id(business_id)
    data = request.get_json(silent=True) or {}
    customer_name = (data.get("customer_name") or "").strip()
    service = (data.get("service") or "").strip() or None
    if not customer_name:
        return jsonify(error="Customer name is required."), 400

    tz = booking.tz_for(business)
    try:
        local_dt = datetime.combine(date_type.fromisoformat(data.get("date", "")), datetime.strptime(data.get("time", ""), "%H:%M").time(), tzinfo=tz)
    except ValueError:
        return jsonify(error="Invalid date or time."), 400

    # The app's booking sheet defaults to the business-wide slot length but lets the
    # owner pick a different duration for this single appointment - same "best-effort"
    # fallback to the business default on garbage input as app.py's create_appointment_route.
    try:
        duration_minutes = int(data.get("duration"))
    except (TypeError, ValueError):
        duration_minutes = get_slot_duration(business_id)
    if not (SLOT_DURATION_MIN <= duration_minutes <= SLOT_DURATION_MAX):
        duration_minutes = get_slot_duration(business_id)
    end_dt = local_dt + timedelta(minutes=duration_minutes)
    is_bookable = (
        local_dt >= booking.now_local(tz)
        and booking.slot_within_business_hours(business[7], local_dt, end_dt, tz)
        and booking.is_slot_free(business_id, local_dt, end_dt)
    )
    if not is_bookable:
        return jsonify(error="book_error"), 409

    create_appointment(business_id, customer_name, local_dt, end_dt, service=service, source="manual")
    return jsonify(success=True)


@api.route("/schedule/appointments/<int:appointment_id>/cancel", methods=["POST"])
@require_api_auth
def api_cancel_appointment(appointment_id):
    from app import BETA_FREE_MODE  # deferred to avoid a circular import at module load

    if not (BETA_FREE_MODE or has_pro_access(request.business_id)):
        return jsonify(error="forbidden"), 403
    appointment = get_appointment(appointment_id)
    if appointment is None or appointment[1] != request.business_id:
        return jsonify(error="not found"), 404
    cancel_appointment(appointment_id)
    return jsonify(success=True)


@api.route("/schedule/slot-duration", methods=["POST"])
@require_api_auth
def api_update_slot_duration():
    from app import BETA_FREE_MODE, SLOT_DURATION_CHOICES, SLOT_DURATION_MIN, SLOT_DURATION_MAX  # deferred to avoid a circular import at module load

    if not (BETA_FREE_MODE or has_pro_access(request.business_id)):
        return jsonify(error="forbidden"), 403
    data = request.get_json(silent=True) or {}
    try:
        minutes = int(data.get("slot_duration", 30))
    except (ValueError, TypeError):
        minutes = 30
    # Anything in the quick-pick list is always allowed; anything else (the "Custom"
    # option in the app's duration picker) just needs to be a sane number of minutes.
    if minutes not in SLOT_DURATION_CHOICES and not (SLOT_DURATION_MIN <= minutes <= SLOT_DURATION_MAX):
        minutes = 30
    set_slot_duration(request.business_id, minutes)
    return jsonify(success=True, slot_duration=minutes)


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
        donation_link=os.getenv("DONATION_LINK", "").strip() or None,
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

    _, business_id, sender, subject, question, suggested_reply, sender_igsid, channel, sender_wa_id, sender_psid = conversation
    if business_id != request.business_id:
        return jsonify(error="forbidden"), 403

    from app import send_channel_reply  # deferred to avoid a circular import at module load

    business = business_by_id(business_id)
    debug_context = f"reply-send-debug conversation_id={conversation_id} channel={channel} sender_igsid={sender_igsid} sender_wa_id={sender_wa_id} sender_psid={sender_psid}"
    result = send_channel_reply(conversation, business, final_reply)
    log_client_error(business_id, f"{channel} send OK: {result}", "", debug_context, "")

    mark_sent(conversation_id, final_reply)

    if business_stats(business_id)["total_sent"] == 1:
        log_event("first_reply_sent", business_id=business_id)

    return jsonify(message="sent")


@api.route("/settings", methods=["GET"])
@require_api_auth
def api_get_settings():
    from database import PHONE_LOGIN_ENABLED  # deferred: picks up the live value, not one frozen at module load

    business = business_by_id(request.business_id)
    return jsonify(
        name=business[1],
        contact_email=business[3],
        working_hours=business[7],
        price_list=business[8],
        faq=business[9],
        phone_number=business[10] or "",
        phone_verified=business[12],
        sms_verification_available=sms_configured(),
        phone_login_enabled=PHONE_LOGIN_ENABLED,
        upsell_pairs=get_upsell_pairs(request.business_id) or "",
    )


@api.route("/settings", methods=["POST"])
@require_api_auth
def api_update_settings():
    from app import BETA_FREE_MODE  # deferred to avoid a circular import at module load
    from database import PHONE_LOGIN_ENABLED  # deferred: picks up the live value, not one frozen at module load

    data = request.get_json(silent=True) or {}
    business_id = request.business_id
    business = business_by_id(business_id)

    name = data.get("name", business[1])
    contact_email = data.get("contact_email", business[3]).strip()
    phone_number = data.get("phone_number", business[10] or "").strip()
    working_hours = data.get("working_hours", business[7])
    price_list = data.get("price_list", business[8])
    faq = data.get("faq", business[9])

    if not contact_email:
        return jsonify(error="Contact email is required."), 400

    new_password = data.get("new_password") or None
    if new_password:
        if not check_password_hash(business[2], data.get("current_password", "")):
            return jsonify(error="Current password is incorrect."), 400
        if len(new_password) < 8:
            return jsonify(error="New password must be at least 8 characters."), 400

    try:
        update_business(business_id, name, contact_email, phone_number or None, working_hours, price_list, faq)
    except UniqueViolation:
        return jsonify(error="That contact email or phone number is already taken."), 409

    # Mirrors update_business()'s own CASE-on-old-value logic (index 10 phone_number, 12
    # phone_verified from the business row fetched above, before this update) instead of
    # an extra round-trip to re-fetch it.
    phone_verified = business[12] and phone_number == (business[10] or "")

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
        message="Saved.", name=name, contact_email=contact_email,
        phone_number=phone_number, phone_verified=phone_verified,
        sms_verification_available=sms_configured(),
        phone_login_enabled=PHONE_LOGIN_ENABLED,
        working_hours=working_hours, price_list=price_list, faq=faq,
        upsell_pairs=upsell_pairs,
    )


# Website media (logo/gallery photos/hero video) - mobile app's Bearer-authed equivalent
# of app.py's session-authed /api/onboarding/media, calling the exact same
# business_media.py rules so a photo uploaded from the app and one uploaded from the web
# onboarding wizard behave identically (same limits, same replace-on-upload for logo/
# hero_video, same Cloudinary cleanup on delete).
@api.route("/media", methods=["GET"])
@require_api_auth
def api_list_media():
    return jsonify(media=business_media.list_media(request.business_id))


@api.route("/media", methods=["POST"])
@require_api_auth
@limiter.limit("60 per hour")
def api_upload_media():
    role = request.form.get("role", "")
    file_storage = request.files.get("file")
    try:
        item = business_media.upload_media(request.business_id, role, file_storage)
    except business_media.MediaError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(**item)


@api.route("/media/<int:media_id>", methods=["DELETE"])
@require_api_auth
def api_delete_media(media_id):
    try:
        business_media.delete_media(request.business_id, media_id)
    except business_media.MediaError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(deleted=True)


# Source documents (PDF/Word) uploaded as extra factual context for AI page generation -
# mobile app's Bearer-authed equivalent of app.py's session-authed
# /api/onboarding/document, calling the exact same business_documents.py rules.
@api.route("/documents", methods=["GET"])
@require_api_auth
def api_list_documents():
    return jsonify(documents=business_documents.list_documents(request.business_id))


@api.route("/documents", methods=["POST"])
@require_api_auth
@limiter.limit("20 per hour")
def api_upload_document():
    file_storage = request.files.get("file")
    try:
        item = business_documents.upload_document(request.business_id, file_storage)
    except business_documents.DocumentError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(**item)


@api.route("/documents/<int:document_id>", methods=["DELETE"])
@require_api_auth
def api_delete_document(document_id):
    try:
        business_documents.delete_document(request.business_id, document_id)
    except business_documents.DocumentError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(deleted=True)


# The business's own hosted public page (slug/tagline/about/theme/published) - mobile's
# Bearer-authed equivalent of app.py's session-authed /profile/page (save_business_page)
# and settings()'s own page_row read, the "My Website" tab's own screen (see app.js). Web
# and mobile share the exact same business_pages row/upsert_business_page() call, so a
# page edited from either always reflects on both.
@api.route("/page", methods=["GET"])
@require_api_auth
def api_get_page():
    business_id = request.business_id
    page_row = get_business_page(business_id)
    page = None if page_row is None else {
        "slug": page_row[2], "published": page_row[3], "tagline": page_row[4], "about": page_row[5],
        "theme": page_row[9], "custom_theme_json": page_row[10],
        "pending_custom_domain": page_row[11], "custom_domain": page_row[12], "custom_domain_status": page_row[13],
    }

    # Same "next 7 days, confirmed only" window as the website's own site-overview home()
    # route (see app.py) - kept here (not a separate endpoint) since the My Site tab is
    # the only mobile screen that needs this summary.
    business = request.business
    tz = booking.tz_for(business)
    now = booking.now_local(tz)
    upcoming_rows = appointments_in_range(business_id, now, now + timedelta(days=7))[:5]
    upcoming_appointments = [
        {"customer_name": row[1], "service": row[3], "start_time": row[4].astimezone(tz).isoformat()}
        for row in upcoming_rows
    ]

    # A Design pick made before this business ever saved a page (see api_save_page's
    # blank-slug branch below) - top-level, not nested in `page`, since `page` staying
    # None when no page exists yet is relied on elsewhere (app.js's mysite-website-preview/
    # mysite-view-site-link visibility). Always present in the response, even once a real
    # page exists, for a uniform shape - app.js only reads these while page is still None.
    return jsonify(
        page=page,
        upcoming_appointments=upcoming_appointments,
        preferred_theme=business[22],
        preferred_custom_theme_json=business[23],
    )


@api.route("/page", methods=["POST"])
@require_api_auth
def api_save_page():
    data = request.get_json(silent=True) or {}
    business_id = request.business_id

    slug = pages.normalize_slug(data.get("slug") or "")
    tagline = (data.get("tagline") or "").strip()[:200] or None
    about = (data.get("about") or "").strip()[:2000] or None
    published = bool(data.get("published"))

    theme = data.get("theme")
    custom_theme_json = None
    if theme == themes.CUSTOM_THEME_ID:
        custom_theme_json = data.get("custom_theme_json") or None
        if not custom_theme_json:
            theme = None
    elif theme not in themes.THEMES:
        theme = None

    # The real external domain (mihajlo-prodaja.com) picked via the mobile app's
    # "Choose" button on an /api/check-domains "available" result - see
    # domain_lookup.normalize_pending_domain for why this is re-validated here rather
    # than trusted as-is. Silently dropped (never a 400) if malformed, same "not
    # something to hard-fail the whole save over" treatment invalid theme gets above.
    pending_custom_domain = domain_lookup.normalize_pending_domain(data.get("pending_custom_domain"))

    # A Design pick made before this business has ever set a real web address - no page
    # row exists yet (get_business_page is None) and no slug was even attempted (blank,
    # not just invalid). Persist ONLY the theme preference on businesses and return
    # early - tagline/about/published are silently ignored in THIS branch specifically
    # because the client-side caller for this case (mobile app.js's
    # saveMysitePreferredTheme) never sends them, by design. A request that DOES include
    # a non-blank invalid slug still falls through to the normal 400 below, unchanged.
    if not slug and get_business_page(business_id) is None:
        set_preferred_theme(business_id, theme, custom_theme_json)
        log_event("business_page_theme_preference_saved", business_id=business_id)
        return jsonify(message="Saved.")

    if not pages.is_valid_slug(slug):
        return jsonify(error="Please choose a web address using only lowercase letters, numbers, and hyphens."), 400

    try:
        upsert_business_page(business_id, slug, published, tagline, about, None, theme, custom_theme_json, pending_custom_domain)
    except UniqueViolation:
        return jsonify(error="That web address is already taken - please choose another."), 409

    log_event("business_page_saved", business_id=business_id)

    # Publishing with a still-unpurchased domain pick is what starts buying it - but
    # NOT directly: the domain is charged to the business itself (a separate $15/yr
    # Paddle subscription, see paddle_billing.domain_price_id), not bundled into their
    # app subscription, so publishing here only flags that a checkout is needed.
    # domain_registrar.purchase_domain() only actually runs once app.py's paddle_webhook
    # confirms that charge went through (see set_custom_domain_purchasing there) - never
    # from this request. custom_domain_status not yet 'purchasing'/'active' is what
    # keeps this from re-asking for checkout on every save once one's already underway.
    response = {"message": "Saved."}
    if published and pending_custom_domain:
        page_row = get_business_page(business_id)
        if page_row and page_row[13] not in ("purchasing", "active"):
            response["domain_checkout_required"] = True
            response["domain"] = pending_custom_domain

    return jsonify(**response)


# Drafts tagline/about/hours/prices/faq from a short owner-written description - mobile's
# Bearer-authed equivalent of app.py's session-authed /profile/generate-page
# (generate_business_page). Returns the draft as plain JSON rather than re-rendering a
# page; app.js fills the "My Website" tab's fields with it for the owner to review before
# tapping Save, same "never writes anything on its own" contract as the web route.
@api.route("/generate-page", methods=["POST"])
@require_api_auth
@limiter.limit("10 per hour")
def api_generate_page():
    business_id = request.business_id
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify(error="Describe your business first so the AI has something to draft from."), 400

    try:
        draft = generate_page_draft(description, document_text=business_documents.combined_document_text(business_id))
    except ValueError:
        return jsonify(error="Couldn't generate a draft from that - please try describing your business differently."), 400

    # Same "only suggest a web address for an owner who doesn't already have one" rule
    # as app.py's generate_business_page() - app.js only pre-fills the field when it's
    # still empty (see mysiteSubmitDescription), but computing an already-taken/invalid
    # suggestion here would be a pointless round trip either way.
    existing_page = get_business_page(business_id)
    existing_slug = existing_page[2] if existing_page else ""
    business = business_by_id(business_id)
    suggested_slug = existing_slug or pages.suggest_available_slug(draft.get("slug", ""), business[1], exclude_business_id=business_id)

    return jsonify(
        tagline=draft["tagline"],
        about=draft["about"],
        slug=suggested_slug,
        working_hours=format_working_hours(draft["hours"]),
        price_list=format_price_list(draft["services"]),
        faq=format_faq(draft["faq"]),
    )


# Mobile's Bearer-authed equivalent of app.py's session-authed /profile/check-domains -
# checks whether real, independently-registrable domains (blue-star-cafe.com, not this
# platform's own /p/<slug> web address) are actually free, via domain_lookup.py's RDAP
# lookups. Pure read-only check - nothing saved, nothing purchased.
@api.route("/check-domains", methods=["POST"])
@require_api_auth
@limiter.limit("20 per hour")
def api_check_domains():
    data = request.get_json(silent=True) or {}
    base_name = (data.get("name") or "").strip()
    if not base_name:
        return jsonify(error="Type a name first."), 400
    return jsonify(domains=domain_lookup.check_domains(base_name))


# Voice input for the description field above - mobile's Bearer-authed equivalent of
# app.py's session-authed /api/onboarding/voice-transcribe, same Whisper call via
# page_generator.transcribe_voice_description. Audio is never written to disk or the
# database, same contract as the web route.
@api.route("/voice-transcribe", methods=["POST"])
@require_api_auth
@limiter.limit("20 per hour")
def api_voice_transcribe():
    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        return jsonify(error="No recording received."), 400

    audio_bytes = audio.read(MAX_VOICE_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_VOICE_AUDIO_BYTES:
        return jsonify(error="That recording is too long - keep it under a minute or so."), 400

    try:
        text = transcribe_voice_description(audio_bytes, audio.filename, audio.mimetype)
    except ValueError:
        return jsonify(error="Couldn't make sense of that recording - please try again or type your description instead."), 400
    finally:
        del audio_bytes

    log_event("onboarding_voice_transcribed", business_id=request.business_id)
    return jsonify(text=text)


# AI-generated theme suggestions for the description above - mobile's Bearer-authed
# equivalent of app.py's session-authed /profile/suggest-themes, same ai_theme.py call.
@api.route("/suggest-themes", methods=["POST"])
@require_api_auth
@limiter.limit("10 per hour")
def api_suggest_themes():
    business_id = request.business_id
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify(error="Describe your business first so the AI has something to design from."), 400

    document_text = business_documents.combined_document_text(business_id)
    options = ai_theme.generate_theme_options(description, document_text=document_text)
    if not options:
        return jsonify(error="Couldn't come up with designs from that - please try describing your business differently."), 400

    log_event("theme_suggestions_generated", business_id=business_id)
    return jsonify(themes=options)


# Triggered by the "Verify phone number" button on the Profile screen - unlike email
# verification this is never sent automatically (phone_number is optional and doesn't
# gate anything on its own), only on request. Texts a short code to the phone itself when
# send_sms.sms_configured() is True (real Twilio credentials in place - see .env.example);
# otherwise, or if the SMS send itself throws, falls back to emailing a confirmation link
# to the account's own contact email, same as it's always worked. `channel` in the
# response tells the app which of the two to show (code entry vs. "check your email").
@api.route("/verify-phone/send", methods=["POST"])
@require_api_auth
@limiter.limit("5 per hour")
def api_send_phone_verification():
    from app import send_phone_verification_email, send_phone_verification_sms, PHONE_VERIFY_RESEND_COOLDOWN_SECONDS  # deferred: avoid circular import at module load

    business = business_by_id(request.business_id)
    if not business[10]:
        return jsonify(error="No phone number on file yet - add one first."), 400
    if business[12]:
        return jsonify(error="This phone number is already verified."), 400

    channel = "sms" if sms_configured() else "email"
    if not phone_verify_sent_recently(business[0], PHONE_VERIFY_RESEND_COOLDOWN_SECONDS):
        if channel == "sms":
            try:
                send_phone_verification_sms(business[0], business[1], business[10])
            except Exception:
                send_phone_verification_email(business[0], business[1], business[10], business[3])
                channel = "email"
        else:
            send_phone_verification_email(business[0], business[1], business[10], business[3])
    return jsonify(
        sent=True, channel=channel,
        wait_seconds=phone_verify_resend_wait_seconds(business[0], PHONE_VERIFY_RESEND_COOLDOWN_SECONDS),
    )


# Redeems the SMS code sent by send_phone_verification_sms() above, from the app's own
# code-entry step (shown instead of the "check your email" message whenever the previous
# /verify-phone/send response came back with channel="sms"). Authenticated the same way
# as the rest of the Profile screen's calls (Bearer token), unlike /verify-email-code's
# signup flow which has to pass business_id explicitly because nothing is logged in yet -
# here request.business_id already is the account, so a wrong guess can't be replayed
# against a different one.
@api.route("/verify-phone/confirm", methods=["POST"])
@require_api_auth
@limiter.limit("10 per hour")
def api_confirm_phone_verification():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()

    business = business_by_id(request.business_id)
    if business[12]:
        return jsonify(error="This phone number is already verified."), 400

    if not business_phone_verify_code_matches(business[0], code):
        return jsonify(error="Incorrect or expired code. Please try again."), 400

    mark_phone_verified(business[0])
    log_event("phone_verified", business_id=business[0])
    return jsonify(verified=True)


@api.route("/push-token", methods=["POST"])
@require_api_auth
def api_save_push_token():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="missing token"), 400
    save_push_token(request.business_id, token)
    return jsonify(message="saved")


# In-app notifications (bell/notifications screen) - currently just owner appointment
# reminders (reminders.py), which land here (and as a real OS push, see push-token above)
# instead of an email. Bearer-authed mobile equivalent of app.py's session-authed
# /notifications used by the web dashboard's bell icon.
@api.route("/notifications", methods=["GET"])
@require_api_auth
def api_get_notifications():
    rows = notifications_for_business(request.business_id)
    return jsonify(
        notifications=[
            {
                "id": row[0], "title": row[1], "body": row[2], "kind": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
                "read": row[5] is not None,
            }
            for row in rows
        ],
        unread_count=unread_notification_count(request.business_id),
    )


@api.route("/notifications/read", methods=["POST"])
@require_api_auth
def api_mark_notifications_read():
    data = request.get_json(silent=True) or {}
    mark_notifications_read(request.business_id, data.get("id"))
    return jsonify(message="saved", unread_count=unread_notification_count(request.business_id))


# Instant-apply preferences shown in the app's Settings tab (as opposed to Profile's
# bulk "Save changes" form) - each one is its own small GET/POST pair, same pattern as
# language/theme (except those are local-only, these need the server to actually know).
#
# owner_reminders_enabled/owner_reminder_minutes mirror the website's /settings (see
# app_settings.html + app.py's update_reminders).
@api.route("/notification-preferences", methods=["GET"])
@require_api_auth
def api_get_notification_preferences():
    return jsonify(
        push_enabled=is_push_enabled(request.business_id),
        new_booking_notifications_enabled=is_new_booking_notifications_enabled(request.business_id),
        owner_reminders_enabled=is_owner_reminders_enabled(request.business_id),
        owner_reminder_minutes=get_owner_reminder_minutes(request.business_id),
    )


@api.route("/notification-preferences", methods=["POST"])
@require_api_auth
def api_update_notification_preferences():
    from app import BETA_FREE_MODE, REMINDER_LEAD_MAX_MINUTES  # deferred to avoid a circular import at module load

    data = request.get_json(silent=True) or {}
    business_id = request.business_id
    if "push_enabled" in data:
        set_push_enabled(business_id, bool(data["push_enabled"]))
    if "new_booking_notifications_enabled" in data:
        set_new_booking_notifications_enabled(business_id, bool(data["new_booking_notifications_enabled"]))
    # No-show reminders (Faza 9/11/12) are Pro-only - same server-side gate as the
    # website's /settings/reminders, so a Starter-plan app user can't flip this on
    # (or change its lead time) either.
    if (BETA_FREE_MODE or has_pro_access(business_id)) and (
        "owner_reminders_enabled" in data or "owner_reminder_minutes" in data
    ):
        if "owner_reminders_enabled" in data:
            set_owner_reminders_enabled(business_id, bool(data["owner_reminders_enabled"]))
        if "owner_reminder_minutes" in data:
            current_owner_minutes = get_owner_reminder_minutes(business_id)

            def _clean_minutes(value, fallback):
                try:
                    minutes = int(value)
                except (TypeError, ValueError):
                    return fallback
                return minutes if 1 <= minutes <= REMINDER_LEAD_MAX_MINUTES else fallback

            set_owner_reminder_minutes(business_id, _clean_minutes(data.get("owner_reminder_minutes"), current_owner_minutes))

    return jsonify(
        message="saved",
        push_enabled=is_push_enabled(business_id),
        new_booking_notifications_enabled=is_new_booking_notifications_enabled(business_id),
        owner_reminders_enabled=is_owner_reminders_enabled(business_id),
        owner_reminder_minutes=get_owner_reminder_minutes(business_id),
    )


# Mobile equivalent of the web dashboard's delete-account wizard (see app.py's
# /settings/delete-account and /delete-account) - the app's own 3-screen wizard
# (screen-delete-warning/reason/confirm in mobile/app.js) collects the same password +
# reason before calling this, just Bearer-authed instead of session-based. Requires the
# current password (re-typed on the confirm screen) so a stolen/leftover token alone
# can't nuke the account, same spirit as the password re-check on a settings password
# change further up this file.
@api.route("/delete-account", methods=["POST"])
@require_api_auth
def api_delete_account():
    data = request.get_json(silent=True) or {}
    business = business_by_id(request.business_id)
    if not business or not check_password_hash(business[2], data.get("password", "")):
        return jsonify(error="Wrong password."), 400

    reason_category = data.get("reason_category", "")
    if reason_category not in ACCOUNT_DELETION_REASONS:
        return jsonify(error="Please choose a reason."), 400
    reason_details = (data.get("reason_details") or "").strip()[:1000]

    record_account_deletion(request.business_id, reason_category, reason_details, source="mobile")
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
#
# Read from mobile-bundle.version (written by build_mobile_bundle.py alongside the zip)
# rather than hashed from the zip's own bytes here: that same stamp is also baked into
# mobile/index.html's data-bundle-version attribute, which a freshly built native APK
# carries too (see mobile/app.js's checkForOtaUpdate) - a fresh install needs to be able
# to tell "the builtin bundle already IS this version" apart from "an OTA update actually
# changed something", and that only works if both sides compute the identical value from
# the identical inputs. Hashing the zip container here independently would drift from
# that baked-in value even when the content is byte-identical (zip entries carry
# filesystem mtimes), forcing every fresh install to download a same-content OTA update
# it doesn't need.
_MOBILE_BUNDLE_PATH = os.path.join(os.path.dirname(__file__), "static", "mobile-bundle.zip")
_MOBILE_BUNDLE_VERSION_PATH = os.path.join(os.path.dirname(__file__), "static", "mobile-bundle.version")


@api.route("/mobile-manifest")
def api_mobile_manifest():
    if not os.path.exists(_MOBILE_BUNDLE_PATH) or not os.path.exists(_MOBILE_BUNDLE_VERSION_PATH):
        return jsonify(available=False)
    with open(_MOBILE_BUNDLE_VERSION_PATH, "r") as f:
        version = f.read().strip()
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
