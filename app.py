import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone, date as date_type
from functools import wraps

import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_login_identifier,
    business_by_id, add_business, create_database, delete_business, replies_today_count,
    update_business, business_by_contact_email, save_reset_token, business_by_reset_token, set_new_password,
    link_subscription, update_subscription_status, get_subscription_status,
    link_stripe_subscription, update_stripe_subscription_status,
    effective_subscription_active, has_pro_access,
    business_stats, log_event, event_funnel, recent_client_errors, log_client_error,
    link_instagram_account, business_by_instagram_account_id, already_processed_messages,
    mark_message_processed, save_draft_reply, previous_conversations_with,
    log_webhook_hit, recent_webhook_hits, raw_conversation_rows, is_auto_send_enabled,
    set_auto_send, business_by_username, business_by_phone_number,
    username_taken, contact_email_taken, phone_number_taken,
    archived_conversations_for_business, trashed_conversations_for_business,
    archive_conversation, unarchive_conversation, soft_delete_conversation, restore_conversation,
    get_slot_duration, set_slot_duration, create_appointment, get_appointment, cancel_appointment,
    is_reminders_enabled, set_reminders_enabled, get_upsell_pairs, set_upsell_pairs,
    all_appointments_for_business, get_stripe_customer_id,
)
from send_email import send_email
from generate_reply import generate_reply, generate_reply_with_booking
from support_assistant import answer_support_question
import booking
import stripe_billing
from push_notifications import send_push_to_business
from instagram_api import (
    authorize_url as instagram_authorize_url, exchange_code_for_token, exchange_for_long_lived_token,
    get_account_info, subscribe_account_to_webhook, send_message as send_instagram_message,
    verify_webhook_challenge, verify_webhook_signature, parse_webhook_messages, get_sender_username,
    get_webhook_subscription_status,
)
from database import all_businesses, all_businesses_unfiltered
from api import api as api_blueprint, APK_VERSION
from extensions import limiter

load_dotenv()

app = Flask(__name__)
# Railway terminates HTTPS at its edge and forwards plain HTTP internally, so without
# this Flask has no way to know the original request was HTTPS - url_for(_external=True)
# and request.url_root would generate http:// links (breaks the password reset email
# link, and would break the mobile app's OTA update download since Android blocks
# cleartext HTTP by default). x_proto=1 trusts one hop of X-Forwarded-Proto, which is
# exactly what Railway's own edge adds.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is not set in the environment variables.")

# Session cookie: HTTPONLY (JS can't read it - protects against XSS session theft),
# SAMESITE=Lax (browser won't send it on requests from other sites - protects against
# CSRF combined with CSRFProtect below) and SECURE in production (only sent over HTTPS).
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_DEBUG", "false").lower() != "true"

create_database()

limiter.init_app(app)
csrf = CSRFProtect(app)

app.register_blueprint(api_blueprint)
# The mobile app authenticates with its own Bearer token (see api.py), not the session
# cookie + CSRF token pair used by browser form submissions, so these routes are exempt.
csrf.exempt(api_blueprint)

# Faza 5 (2026-07-21): flipped off - real trial/pricing is live. New accounts now get
# our own self-serve 7-day trial at registration (subscription_status='trialing', see
# add_business()) and, once that runs out without a real Paddle subscription, land on
# the Starter/Pro pricing page (see effective_subscription_active()/trial_expired() in
# database.py). NOTE: the four Paddle prices (Starter/Pro x monthly/yearly) don't exist
# yet as of this commit - checkout buttons show "Coming soon" until PADDLE_PRICE_
# STARTER_MONTHLY/STARTER_YEARLY/PRO_MONTHLY/PRO_YEARLY are set on Railway.
BETA_FREE_MODE = False


@app.context_processor
def inject_beta_free_mode():
    return {"beta_free_mode": BETA_FREE_MODE}


# --- Static asset caching (perf) ---
# Flask/Werkzeug's default static-file serving sends Cache-Control: no-cache, which
# forces the browser to round-trip to the server on every single page navigation just
# to ask "did this change?" - even for style.css, which barely ever changes within a
# session. Fixed by long-caching a small allowlist of assets instead of applying this
# blanket (SEND_FILE_MAX_AGE_DEFAULT) - a blanket default would also long-cache
# /static/mobile-bundle.zip, which the mobile app's OTA updater depends on always being
# able to fetch the latest copy of.
def _static_file_version(filename):
    path = os.path.join(app.static_folder, filename)
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "0"


# style.css is cache-busted via a content-hash query param (see base.html), so it's
# safe to cache "forever" - a content change always means a new URL. The others aren't
# versioned, so they get a moderate cache instead of a full year.
STATIC_VERSIONS = {
    "style.css": _static_file_version("style.css"),
    "htmx.min.js": _static_file_version("htmx.min.js"),
    "site-i18n.js": _static_file_version("site-i18n.js"),
    "pickers.js": _static_file_version("pickers.js"),
}
_FOREVER_CACHE_STATIC_PATHS = {"/static/style.css", "/static/htmx.min.js", "/static/site-i18n.js", "/static/pickers.js"}
_MODERATE_CACHE_STATIC_PATHS = {"/static/manifest.json", "/static/icon-192.png", "/static/icon-512.png"}


@app.context_processor
def inject_static_versions():
    return {"static_versions": STATIC_VERSIONS}


@app.after_request
def cache_static_assets(response):
    if request.path in _FOREVER_CACHE_STATIC_PATHS:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path in _MODERATE_CACHE_STATIC_PATHS:
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# Baseline security headers on every response. CSP allows 'unsafe-inline' for script/
# style deliberately - the site relies on many small inline <script> blocks (support
# widget, scroll-to-top, service worker registration, language picker...) throughout
# base.html and would need a broader nonce-based refactor to tighten further; this still
# meaningfully blocks framing (clickjacking), object/embed content, and cross-origin
# fetches, which cost nothing to add now. Stripe Checkout and the old Paddle overlay are
# both same-origin-initiated redirects/scripts, not iframes, so no extra frame-src is needed.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


@app.after_request
def set_security_headers(response):
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not app.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def login_required(function):
    @wraps(function)
    def wrapped_function(*args, **kwargs):
        if not session.get("business_id"):
            return redirect(url_for("login"))
        # The session can point to a business that no longer exists (e.g. account deleted
        # in another tab) - in that case clear the session and send to login
        if business_by_id(session["business_id"]) is None:
            session.clear()
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapped_function


# Faza 10: once a logged-in business's 7-day self-serve trial (or paid plan) has
# actually lapsed - not just "hasn't upgraded to Pro", see the Starter-vs-Pro
# distinction on /schedule instead - core product pages redirect to /subscription
# rather than just showing a banner (see dashboard.html's older banner for the softer
# in-place warning this replaces on those pages). Only fires for a logged-in business
# (business_id in session); a logged-out visitor is unaffected, since home() itself
# branches on session before ever calling any decorated logic. Must sit UNDER
# @login_required in the decorator stack (closer to the view function) so it only ever
# runs after login is already confirmed.
def subscription_required(function):
    @wraps(function)
    def wrapped_function(*args, **kwargs):
        business_id = session.get("business_id")
        if business_id and not BETA_FREE_MODE and not effective_subscription_active(business_id):
            return redirect(url_for("subscription", trial_ended=1))
        return function(*args, **kwargs)
    return wrapped_function


@app.context_processor
def inject_nav_pro_access():
    """Powers the small "Pro" hint badge on the Schedule nav link (_nav.html) for a
    Starter-plan (or lapsed) business, before they even click in - computed globally
    here rather than passed into every single route's render_template call."""
    business_id = session.get("business_id")
    if not business_id:
        return {"nav_pro_access": None}
    return {"nav_pro_access": BETA_FREE_MODE or has_pro_access(business_id)}


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        business = business_by_login_identifier(username)

        if business and check_password_hash(business[3], password):
            session["business_id"] = business[0]
            session["business_name"] = business[1]
            return redirect(url_for("home"))

        return render_template("login.html", error="Wrong username or password.", message=None)

    return render_template("login.html", error=None, message=request.args.get("message"))


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if request.method == "POST":
        if not request.form.get("accept_terms"):
            return render_template("register.html", error="You must accept the Terms of Service and Privacy Policy.", data=request.form)

        if len(request.form.get("password", "")) < 8:
            return render_template("register.html", error="Password must be at least 8 characters.", data=request.form)

        try:
            new_id = add_business(
                name=request.form["name"],
                username=request.form["username"],
                password=request.form["password"],
                contact_email=request.form["contact_email"],
                working_hours=request.form["working_hours"],
                price_list=request.form["price_list"],
                faq=request.form["faq"],
                phone_number=request.form.get("phone_number") or None,
            )
        except psycopg2.errors.UniqueViolation:
            return render_template("register.html", error="That username, contact email, or phone number is already taken.", data=request.form)

        # Auto-send is offered as a step on the registration form itself (Faza 5, matches
        # the mobile app's own registration wizard) - defaults to off (add_business's own
        # column default) if the checkbox wasn't ticked.
        if request.form.get("auto_send"):
            set_auto_send(new_id, True)

        log_event("register_completed", business_id=new_id)
        session["business_id"] = new_id
        session["business_name"] = request.form["name"]
        # Instagram isn't connected yet - that's a separate OAuth round-trip, can't happen
        # inside this same form POST. connect_instagram() (and home()) enforce it from here.
        return redirect(url_for("connect_instagram"))

    if not session.get("_seen_register"):
        log_event("register_view")
        session["_seen_register"] = True
    return render_template("register.html", error=None, data={})


@app.route("/connect-instagram")
@login_required
def connect_instagram():
    business = business_by_id(session["business_id"])
    if business[5]:  # instagram_account_id already set - nothing to do
        return redirect(url_for("home"))
    return render_template("connect_instagram.html", error=request.args.get("error"))


@app.route("/connect-instagram/start")
@login_required
def connect_instagram_start():
    redirect_uri = url_for("instagram_oauth_callback", _external=True)
    return redirect(instagram_authorize_url(redirect_uri))


@app.route("/instagram-oauth-callback")
def instagram_oauth_callback():
    # Two ways to reach this route: the website flow (logged-in session cookie), and the
    # mobile app flow (no cookie at all - the app only ever holds a Bearer token, never a
    # website session). The mobile app instead identifies itself via the OAuth "state"
    # param, set to a short-lived signed token when it requested the authorize URL from
    # /api/instagram-connect-url - Meta echoes "state" back unmodified on this callback,
    # which is exactly what that parameter is for.
    from api import business_id_from_instagram_connect_state  # deferred: avoid circular import at module load

    mobile_business_id = business_id_from_instagram_connect_state(request.args.get("state"))
    is_mobile_flow = mobile_business_id is not None

    if is_mobile_flow:
        business_id = mobile_business_id
    elif session.get("business_id"):
        business_id = session["business_id"]
    else:
        return redirect(url_for("login"))

    code = request.args.get("code")
    if request.args.get("error") or not code:
        # User declined the Instagram permission prompt, or it otherwise failed before
        # ever giving us a code - not our error, just let them try again.
        if is_mobile_flow:
            return render_template("instagram_connect_mobile.html", success=False)
        return redirect(url_for("connect_instagram", error="Instagram connection was cancelled. Try again whenever you're ready."))

    redirect_uri = url_for("instagram_oauth_callback", _external=True)
    try:
        short_lived = exchange_code_for_token(code, redirect_uri)
        long_lived_token = exchange_for_long_lived_token(short_lived["access_token"])
        account_info = get_account_info(long_lived_token)
        subscribe_account_to_webhook(long_lived_token)
        link_instagram_account(
            business_id,
            str(account_info["user_id"]),
            account_info.get("username"),
            long_lived_token,
        )
    except requests.RequestException:
        if is_mobile_flow:
            return render_template("instagram_connect_mobile.html", success=False)
        return redirect(url_for("connect_instagram", error="Couldn't connect to Instagram - please try again."))

    log_event("instagram_connected", business_id=business_id)
    if is_mobile_flow:
        return render_template("instagram_connect_mobile.html", success=True)
    return redirect(url_for("home"))


def _email_password_reset_link(business):
    """Shared final step once an account has been found by whichever lookup (email,
    username, or phone number) - issues a fresh single-use token and emails the link to
    the account's contact email (the only channel we can actually deliver it through -
    there's no SMS integration, so even the phone-number lookup path ends here)."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    save_reset_token(business[0], token, expires)

    link = url_for("reset_password", token=token, _external=True)
    send_email(
        recipient=business[4],
        subject="Password reset - Camilius",
        body=(
            f"Hi,\n\n"
            f"Someone (hopefully you) requested a password reset for '{business[1]}'.\n\n"
            f"Your username is: {business[2]}\n\n"
            f"Click the link to set a new password (valid for 1 hour):\n{link}\n\n"
            f"If this wasn't you, feel free to ignore this email."
        ),
        business_name="Camilius",
        reply_to=os.getenv("ALERT_EMAIL") or business[4],
    )


def request_password_reset(identifier):
    """Shared by the web /forgot-password route and the mobile app's /api/forgot-password.
    The field is labeled "Email or username" - looks up the account by either, and if
    found, emails a reset link. Returns True/False so the caller can tell the user
    whether that email/username is actually registered (a deliberate product choice -
    see the callers for the tradeoff)."""
    business = business_by_contact_email(identifier) or business_by_username(identifier)
    if not business:
        return False
    _email_password_reset_link(business)
    return True


def request_password_reset_by_phone(phone_number):
    """"Find by mobile number" recovery path - same idea as request_password_reset, but
    looks the account up by its registered phone number instead. There's still no SMS
    channel, so the actual reset link is emailed to the contact email already on file,
    same as every other path."""
    business = business_by_phone_number(phone_number)
    if not business:
        return False
    _email_password_reset_link(business)
    return True


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form["identifier"]
        if not request_password_reset(identifier):
            return render_template(
                "forgot_password.html",
                error="No account found with that email or username.",
                identifier=identifier,
            )
        return render_template(
            "forgot_password.html",
            message="We've sent a password reset link to the email on file.",
        )

    return render_template("forgot_password.html", message=None)


@app.route("/forgot-password/phone", methods=["POST"])
@limiter.limit("5 per hour")
def forgot_password_phone():
    phone_number = request.form.get("phone_number", "").strip()
    if not request_password_reset_by_phone(phone_number):
        return render_template(
            "forgot_password.html",
            error="No account found with that phone number.",
            phone_mode=True,
            phone_number=phone_number,
        )
    return render_template(
        "forgot_password.html",
        message="We found your account - we've sent a password reset link to the email on file.",
    )


# Behind both the web Create Account form (register.html) and the Profile settings form
# (settings.html only exposes name/hours/prices/faq for now, but Profile edits live in the
# mobile app - see api.py's own /api/check-availability for that side) - lets the frontend
# show an inline "already taken" error as the user types, instead of only finding out from
# a UniqueViolation after submitting. Session-aware: a logged-in caller's OWN current
# value is excluded so editing a field without changing it never flags itself as taken.
_AVAILABILITY_CHECKS = {
    "username": username_taken,
    "contact_email": contact_email_taken,
    "phone_number": phone_number_taken,
}


@app.route("/check-availability", methods=["POST"])
@limiter.limit("60 per minute")
def check_availability():
    data = request.get_json(silent=True) or {}
    value = (data.get("value") or "").strip()
    check = _AVAILABILITY_CHECKS.get(data.get("field"))
    if check is None:
        abort(400)
    if not value:
        return jsonify(taken=False)
    return jsonify(taken=check(value, exclude_business_id=session.get("business_id")))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    business = business_by_reset_token(token)

    if business is None:
        return render_template("reset_password.html", invalid=True)

    if request.method == "POST":
        new_password = request.form["password"]
        if len(new_password) < 8:
            return render_template("reset_password.html", invalid=False, error="Password must be at least 8 characters.")
        set_new_password(business[0], new_password)
        return redirect(url_for("login", message="Password changed. Log in with your new password."))

    return render_template("reset_password.html", invalid=False, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    business_id = session["business_id"]
    business = business_by_id(business_id)
    # indices: 0 id, 1 name, 2 username, 4 contact_email, 5 instagram_account_id,
    # 6 instagram_username, 8 working_hours, 9 price_list, 10 faq, 11 phone_number
    contact_email = business[4]
    instagram_username = business[6]
    pro_access = BETA_FREE_MODE or has_pro_access(business_id)

    if request.method == "POST":
        name = request.form["name"]
        working_hours = request.form["working_hours"]
        price_list = request.form["price_list"]
        faq = request.form["faq"]

        # Username/email/phone aren't editable from this page (the mobile app's Profile
        # screen is where that lives) - carry the existing values through unchanged.
        update_business(business_id, name, business[2], contact_email, business[11], working_hours, price_list, faq)
        session["business_name"] = name

        # AI upsell pairs (Faza 9, Pro-only) - a separate call/column from the rest of
        # the profile fields above, and only saved for Pro-access businesses so a raw
        # POST can't bypass the Starter-plan gate the settings.html form UI enforces.
        upsell_pairs = get_upsell_pairs(business_id)
        if pro_access and "upsell_pairs" in request.form:
            upsell_pairs = request.form["upsell_pairs"]
            set_upsell_pairs(business_id, upsell_pairs)

        return render_template(
            "settings.html",
            error=None,
            message="Your details have been saved.",
            pro_access=pro_access,
            business={
                "name": name, "contact_email": contact_email, "instagram_username": instagram_username,
                "working_hours": working_hours, "price_list": price_list, "faq": faq, "upsell_pairs": upsell_pairs,
            },
        )

    return render_template(
        "settings.html",
        error=None,
        message=None,
        pro_access=pro_access,
        business={
            "name": business[1], "contact_email": contact_email, "instagram_username": instagram_username,
            "working_hours": business[8], "price_list": business[9], "faq": business[10],
            "upsell_pairs": get_upsell_pairs(business_id),
        },
    )


# Distinct from /settings above (renamed "Profile" in the nav - business info the AI
# uses) - this is app-level preferences: legal, help, and account-data actions, mirroring
# the mobile app's own Settings screen split.
@app.route("/app-settings")
@login_required
def app_settings():
    business_id = session["business_id"]
    return render_template(
        "app_settings.html",
        auto_send=is_auto_send_enabled(business_id),
        reminders_enabled=is_reminders_enabled(business_id),
        pro_access=BETA_FREE_MODE or has_pro_access(business_id),
    )


# Website equivalent of the mobile app's Auto-send toggle (see api.py's
# /api/notification-preferences) - same underlying businesses.auto_send column, just
# session-authenticated instead of Bearer-token, since the website never had a way to
# change this at all before now.
@app.route("/app-settings/auto-send", methods=["POST"])
@login_required
def update_auto_send():
    set_auto_send(session["business_id"], bool(request.form.get("auto_send")))
    return redirect(url_for("app_settings"))


# No-show reminders toggle (Faza 9, Pro-only) - Starter-plan businesses can't turn this
# on even via a raw POST; the checkbox itself is locked/hidden for them in app_settings.html.
@app.route("/app-settings/reminders", methods=["POST"])
@login_required
def update_reminders():
    business_id = session["business_id"]
    if BETA_FREE_MODE or has_pro_access(business_id):
        set_reminders_enabled(business_id, bool(request.form.get("reminders_enabled")))
    return redirect(url_for("app_settings"))


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    delete_business(session["business_id"])
    session.clear()
    return redirect(url_for("login"))


def business_export_data(business_id):
    """Shared by the web /export-data route (session auth) and the mobile app's
    token-gated /export-data/<token> (see api.py's /api/export-data-link) - a
    self-serve GDPR-style export of a business's own data. Deliberately excludes
    secrets (password hash, encrypted Instagram access token) even though those
    fields aren't otherwise sensitive to the business itself, since there's no
    reason to ever put them in a downloadable file."""
    business = business_by_id(business_id)
    conversations = conversations_for_business(business_id)
    return {
        "business": {
            "name": business[1],
            "username": business[2],
            "contact_email": business[4],
            "instagram_username": business[6],
            "working_hours": business[8],
            "price_list": business[9],
            "faq": business[10],
        },
        "conversations": [
            {
                "sender": c[1],
                "subject": c[2],
                "question": c[3],
                "suggested_reply": c[4],
                "final_reply": c[5],
                "status": c[6],
                "created_at": c[7].isoformat() if c[7] else None,
            }
            for c in conversations
        ],
        "appointments": [
            {
                "customer_name": a[0],
                "service": a[1],
                "start_time": a[2].isoformat() if a[2] else None,
                "end_time": a[3].isoformat() if a[3] else None,
                "status": a[4],
                "source": a[5],
                "notes": a[6],
                "created_at": a[7].isoformat() if a[7] else None,
            }
            for a in all_appointments_for_business(business_id)
        ],
    }


def _export_data_response(business_id):
    response = jsonify(business_export_data(business_id))
    response.headers["Content-Disposition"] = "attachment; filename=camilius-ai-data-export.json"
    return response


@app.route("/export-data")
@login_required
def export_data():
    return _export_data_response(session["business_id"])


# Mobile-app equivalent of /export-data above - the app can't use session-cookie auth
# (Bearer token only, see api.py), and Capacitor's Browser plugin (used to trigger the
# actual download, same as the APK-update flow) can't attach an Authorization header to
# the request it opens - so business identity is carried via a short-lived signed token
# instead, requested first through /api/export-data-link. Same bridge pattern as the
# Instagram-connect OAuth "state" token, see api.py's make_instagram_connect_state.
@app.route("/export-data/<token>")
def export_data_mobile(token):
    from api import business_id_from_export_link_token  # deferred: avoid circular import at module load

    business_id = business_id_from_export_link_token(token)
    if business_id is None:
        abort(404)
    return _export_data_response(business_id)


@app.errorhandler(404)
def page_not_found(e):
    return render_template("error.html", code=404, message="The page you're looking for doesn't exist or has moved."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="A server error occurred. Please try again in a minute."), 500


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


# Swipeable onboarding slides shown once, only inside the installed Android app
# (never in a regular browser) - see the redirect check in landing.html.
@app.route("/intro")
def intro():
    return render_template("intro.html")


@app.route("/download")
def download():
    return render_template("download.html")


# Serves the APK with Content-Disposition: attachment (Flask's default static file
# route sends "inline", which some browsers/in-app contexts fail to turn into an
# actual download for a binary file type they can't display).
#
# The downloaded filename includes the app version so Chrome treats each release as a
# new file instead of asking "Download again?" for a same-named file it already has.
# (APK_VERSION itself now lives in api.py, since /api/apk-version needs it too.)


@app.route("/download/camilius-ai.apk")
def download_apk():
    return send_from_directory(
        app.static_folder,
        "camilius-ai.apk",
        as_attachment=True,
        download_name=f"camilius-ai-v{APK_VERSION}.apk",
    )


# Founder-only diagnostic: shows the state of every connected Instagram account and,
# crucially, whether it's actually subscribed to the messages webhook (the #1 reason a
# connected account's DMs silently never arrive). `?resubscribe=1` also re-runs the
# subscribe call and reports the result. Gated by the same secret token as the other
# /admin routes. Safe to leave in - read-only unless resubscribe is explicitly passed.
@app.route("/admin/instagram-debug")
def admin_instagram_debug():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    resubscribe = request.args.get("resubscribe") == "1"
    result = []
    for business in all_businesses():
        # indices: 0 id, 1 name, 5 instagram_account_id, 6 instagram_username, 7 access_token
        access_token = business[7]
        entry = {
            "business_id": business[0],
            "name": business[1],
            "instagram_account_id": business[5],
            "instagram_username": business[6],
            "has_token": bool(access_token),
        }
        if access_token:
            try:
                entry["current_subscription"] = get_webhook_subscription_status(access_token)
            except Exception as e:
                entry["current_subscription_error"] = repr(e)
            if resubscribe:
                try:
                    entry["resubscribe_result"] = subscribe_account_to_webhook(access_token)
                except Exception as e:
                    entry["resubscribe_error"] = repr(e)
        result.append(entry)

    hits = [
        {"id": h[0], "note": h[1], "body": h[2], "created_at": h[3].isoformat() if h[3] else None}
        for h in recent_webhook_hits(20)
    ]
    return jsonify(businesses=result, recent_webhook_hits=hits)


# Founder-only lookup/cleanup tool - unlike /admin/instagram-debug (which only lists
# businesses that finished connecting Instagram), this finds EVERY business, including
# abandoned test/onboarding-incomplete accounts, and can delete one by id. Same secret-
# token gating as every other /admin route.
@app.route("/admin/businesses")
def admin_businesses():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    search = (request.args.get("q") or "").strip().lower()
    result = []
    for business in all_businesses_unfiltered():
        # indices: 0 id, 1 name, 2 username, 4 contact_email, 12 subscription_status, 13 created_at
        if search and search not in business[1].lower() and search not in business[2].lower() and search not in business[4].lower():
            continue
        result.append({
            "id": business[0],
            "name": business[1],
            "username": business[2],
            "contact_email": business[4],
            "instagram_username": business[6],
            "subscription_status": business[12],
            "created_at": business[13].isoformat() if business[13] else None,
        })
    return jsonify(businesses=result)


@app.route("/admin/businesses/<int:business_id>/delete", methods=["POST"])
@csrf.exempt  # admin tooling authenticated by token (below), not a browser session
def admin_delete_business(business_id):
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    business = business_by_id(business_id)
    if business is None:
        abort(404)
    delete_business(business_id)
    return jsonify(deleted=True, business_id=business_id, name=business[1])


# Private funnel dashboard for the founder only - gated by a secret token rather than a
# real login, since the app has no concept of an "admin" account separate from a business.
@app.route("/admin/analytics")
def admin_analytics():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    funnel = event_funnel(days=30)
    steps = [
        ("landing_view", "Landing page views"),
        ("register_view", "Opened registration form"),
        ("register_completed", "Finished registration"),
        ("first_reply_sent", "Sent their first reply"),
    ]
    return render_template("admin_analytics.html", steps=steps, funnel=funnel)


# Self-hosted crash/error log for the Android app - same secret-token gating as
# /admin/analytics, no separate crash-reporting account needed during beta.
@app.route("/admin/client-errors")
def admin_client_errors():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    errors = recent_client_errors(limit=100)
    return render_template("admin_client_errors.html", errors=errors)


# Temporary diagnostic: dumps the raw conversation rows straight from the DB (status,
# suggested_reply, final_reply as actually stored) so we can see the ground truth behind
# the dashboard showing "None" replies, instead of inferring it from the rendered page.
# Remove once the reply bug is confirmed fixed.
@app.route("/admin/conversations-debug")
def admin_conversations_debug():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    rows = raw_conversation_rows(limit=30)
    lines = []
    for r in rows:
        lines.append({
            "id": r[0],
            "business_id": r[1],
            "status": repr(r[2]),
            "suggested_reply": repr(r[3]),
            "final_reply": repr(r[4]),
            "created_at": r[5].isoformat() if r[5] else None,
        })
    return jsonify(lines)


# Proves to Android that the "Camilius" APK and this website are owned by the
# same party, so the installed app opens fullscreen without a browser URL bar.
# The fingerprint matches the signing key in ai-auto-reply-secrets/android-signing/.
@app.route("/.well-known/assetlinks.json")
def android_asset_links():
    return jsonify([
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.ai_auto_reply_mail.app.twa",
                "sha256_cert_fingerprints": [
                    "80:57:F1:F9:19:CA:E9:C2:D7:15:28:02:55:DD:2D:ED:76:B8:28:D9:94:F6:09:CE:77:AC:48:60:33:D1:0F:53"
                ],
            },
        }
    ])


DAILY_REPLY_LIMIT = 50  # also enforced in the /instagram-webhook handler below


@app.route("/")
@subscription_required
def home():
    # Logged-out visitors see the public landing page (marketing + call to register)
    # instead of a redirect to login. Logged-in visitors get the dashboard.
    business = session.get("business_id") and business_by_id(session["business_id"])
    if not business:
        # Read the flag before clear() wipes it - it's what lets us count each
        # visitor session once instead of logging every page refresh.
        already_seen = session.get("_seen_landing", False)
        session.clear()
        session["_seen_landing"] = True
        if not already_seen:
            log_event("landing_view")
        return render_template("landing.html")

    if not business[5]:  # instagram_account_id not set - can't do anything without it
        return redirect(url_for("connect_instagram"))

    conversations = conversations_for_business(session["business_id"])
    archived = archived_conversations_for_business(session["business_id"])
    trashed = trashed_conversations_for_business(session["business_id"])

    # Filter by status via ?filter=pending/sent/archived/trash (default: all)
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
    today = replies_today_count(session["business_id"])
    stats = business_stats(session["business_id"])

    return render_template(
        "dashboard.html",
        conversations=shown,
        business_name=session["business_name"],
        filter_status=filter_status,
        total_count=len(conversations),
        pending_count=pending_count,
        archived_count=len(archived),
        trash_count=len(trashed),
        today=today,
        daily_limit=DAILY_REPLY_LIMIT,
        sent=request.args.get("sent"),
        send_error=request.args.get("send_error"),
        subscription_active_flag=effective_subscription_active(session["business_id"]),
        stats=stats,
    )


@app.route("/conversations/<int:conversation_id>/archive", methods=["POST"])
@login_required
def archive_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    archive_conversation(conversation_id)
    return redirect(url_for("home", filter=request.form.get("from_filter", "all")))


@app.route("/conversations/<int:conversation_id>/unarchive", methods=["POST"])
@login_required
def unarchive_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    unarchive_conversation(conversation_id)
    return redirect(url_for("home", filter=request.form.get("from_filter", "archived")))


@app.route("/conversations/<int:conversation_id>/delete", methods=["POST"])
@login_required
def delete_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    soft_delete_conversation(conversation_id)
    return redirect(url_for("home", filter=request.form.get("from_filter", "all")))


@app.route("/conversations/<int:conversation_id>/restore", methods=["POST"])
@login_required
def restore_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    restore_conversation(conversation_id)
    return redirect(url_for("home", filter=request.form.get("from_filter", "trash")))


@app.route("/send/<int:conversation_id>", methods=["POST"])
@login_required
def send_reply(conversation_id):
    final_reply = request.form["reply"]

    _, business_id, sender, subject, question, suggested_reply, sender_igsid = get_conversation(conversation_id)

    # Security check: this conversation must belong to the logged-in business,
    # otherwise someone could guess other people's IDs in the URL and send replies on their behalf
    if business_id != session["business_id"]:
        abort(403)

    business = business_by_id(business_id)

    # Temporary diagnostic: the dashboard was showing "Sent" (Instagram's API accepted
    # the request with 2xx) while the reply never reached the customer's real Instagram
    # DM thread - logging Instagram's own response body here (viewable at
    # /admin/client-errors) so we can see exactly what it returned instead of guessing.
    debug_context = f"web-reply-send-debug conversation_id={conversation_id} sender_igsid={sender_igsid} ig_account_id={business[5]} final_reply={final_reply!r}"
    try:
        # Instagram only allows sending within 24h of the customer's last message (same
        # policy every DM platform has) - a send past that window fails here, which is an
        # expected business outcome (not a bug), hence the friendly redirect rather than
        # letting it 500.
        result = send_instagram_message(sender_igsid, final_reply, business[7], business[5])
        log_client_error(business_id, f"instagram send OK: {result}", "", debug_context, "")
    except requests.RequestException as e:
        error_body = e.response.text if getattr(e, "response", None) is not None else str(e)
        log_client_error(business_id, f"instagram send FAILED: {error_body}", "", debug_context, "")
        return redirect(url_for("home", send_error=1))

    mark_sent(conversation_id, final_reply)

    # Log the moment a business sends its very first reply - the key "aha" point in the funnel
    if business_stats(business_id)["total_sent"] == 1:
        log_event("first_reply_sent", business_id=business_id)

    # sent=1 -> dashboard shows a confirmation that the reply went out
    return redirect(url_for("home", sent=1))


# --- Schedule / Smart Booking (Faza 8) ---
#
# The calendar itself (view/manually add/cancel appointments) is available to every
# business regardless of plan - it's what the AI booking tool below writes into. Only
# the AI auto-booking a slot directly from an Instagram DM (see _process_instagram_
# message) is Pro-plan-exclusive; see has_pro_access() in database.py.

SLOT_DURATION_CHOICES = (15, 30, 45, 60, 90, 120)


@app.route("/schedule")
@login_required
@subscription_required
def schedule():
    business_id = session["business_id"]

    # Faza 10 feature gating: Starter-plan businesses have NO access to Schedule at all
    # (not even the manual calendar, unlike Faza 8/9's softer "AI booking only" gate) -
    # shown a locked "Upgrade to Pro" page instead of any calendar content.
    if not (BETA_FREE_MODE or has_pro_access(business_id)):
        return render_template("schedule.html", locked=True)

    business = business_by_id(business_id)
    working_hours = business[8]
    slot_minutes = get_slot_duration(business_id)

    view = request.args.get("view", "day")
    if view not in ("day", "week"):
        view = "day"
    try:
        selected_date = date_type.fromisoformat(request.args.get("date", ""))
    except ValueError:
        selected_date = booking.now_local().date()

    context = dict(
        locked=False,
        view=view,
        selected_date=selected_date,
        today=booking.now_local().date(),
        slot_minutes=slot_minutes,
        slot_duration_choices=SLOT_DURATION_CHOICES,
        has_any_open_day=any(booking.parse_working_hours(working_hours).values()),
        book_error=request.args.get("book_error"),
    )

    if view == "week":
        week_start = selected_date - timedelta(days=selected_date.weekday())
        days = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            days.append({
                "date": d,
                "iso": d.isoformat(),
                "is_today": d == context["today"],
                "closed": not booking.is_open_on(working_hours, d),
                "appointments": sorted(booking.appointments_for_local_day(business_id, d), key=lambda a: a["start_local"]),
            })
        context["days"] = days
        context["prev_date"] = (week_start - timedelta(days=7)).isoformat()
        context["next_date"] = (week_start + timedelta(days=7)).isoformat()
    else:
        slots = booking.day_slots(working_hours, slot_minutes, selected_date)
        appts_by_start = {a["start_local"].strftime("%H:%M"): a for a in booking.appointments_for_local_day(business_id, selected_date)}
        rows = []
        for s in slots:
            key = s.strftime("%H:%M")
            rows.append({
                "time": key,
                "end_time": (s + timedelta(minutes=slot_minutes)).strftime("%H:%M"),
                "appointment": appts_by_start.get(key),
                "is_past": s < booking.now_local(),
            })
        context["slot_rows"] = rows
        context["prev_date"] = (selected_date - timedelta(days=1)).isoformat()
        context["next_date"] = (selected_date + timedelta(days=1)).isoformat()

    return render_template("schedule.html", **context)


# Mobile-app equivalent of /schedule above (Faza 11) - same token-bridge pattern as
# /subscription/<token>/export-data/<token>, since the day/week calendar UI only exists
# on the website (not worth duplicating natively) and Capacitor's Browser plugin can't
# attach the app's Bearer token to a request it opens. Establishes a real session (see
# the same fix on subscription_mobile above) so booking/canceling from this bridged tab
# actually works instead of silently requiring a login the mobile user never gets asked for.
@app.route("/schedule/<token>")
def schedule_mobile(token):
    from api import business_id_from_schedule_link_token  # deferred: avoid circular import at module load

    business_id = business_id_from_schedule_link_token(token)
    if business_id is None:
        abort(404)
    business = business_by_id(business_id)
    if business:
        session["business_id"] = business_id
        session["business_name"] = business[1]
    return redirect(url_for("schedule"))


@app.route("/schedule/appointments", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def create_appointment_route():
    business_id = session["business_id"]
    if not (BETA_FREE_MODE or has_pro_access(business_id)):
        abort(403)  # Starter-plan bypass attempt - the page itself is locked, see schedule()
    business = business_by_id(business_id)
    date_str = request.form.get("date", "")
    time_str = request.form.get("time", "")
    customer_name = (request.form.get("customer_name") or "").strip()
    service = (request.form.get("service") or "").strip() or None

    try:
        local_dt = datetime.combine(date_type.fromisoformat(date_str), datetime.strptime(time_str, "%H:%M").time(), tzinfo=booking.BUSINESS_TZ)
    except ValueError:
        return redirect(url_for("schedule", date=date_str))

    if customer_name:
        slot_minutes = get_slot_duration(business_id)
        end_dt = local_dt + timedelta(minutes=slot_minutes)
        is_bookable = (
            local_dt >= booking.now_local()
            and booking.slot_within_business_hours(business[8], local_dt, end_dt)
            and booking.is_slot_free(business_id, local_dt, end_dt)
        )
        if is_bookable:
            create_appointment(business_id, customer_name, local_dt, end_dt, service=service, source="manual")
        else:
            return redirect(url_for("schedule", date=date_str, book_error=1))

    return redirect(url_for("schedule", date=date_str))


@app.route("/schedule/appointments/<int:appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment_route(appointment_id):
    if not (BETA_FREE_MODE or has_pro_access(session["business_id"])):
        abort(403)
    appointment = get_appointment(appointment_id)
    if appointment is None or appointment[1] != session["business_id"]:
        abort(404)
    cancel_appointment(appointment_id)
    return redirect(url_for("schedule", date=request.form.get("date", ""), view=request.form.get("view", "day")))


@app.route("/schedule/slot-duration", methods=["POST"])
@login_required
def update_slot_duration():
    if not (BETA_FREE_MODE or has_pro_access(session["business_id"])):
        abort(403)
    try:
        minutes = int(request.form.get("slot_duration", 30))
    except ValueError:
        minutes = 30
    if minutes not in SLOT_DURATION_CHOICES:
        minutes = 30
    set_slot_duration(session["business_id"], minutes)
    return redirect(url_for("schedule", date=request.form.get("date", "")))


# --- Support AI assistant (Faza 8) - floating widget, see base.html ---

@app.route("/support-chat", methods=["POST"])
@limiter.limit("20 per hour")
def support_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message or not isinstance(history, list):
        abort(400)
    # Cap how much of the client-supplied history we forward - it's just a UX nicety for
    # the model's context, not something we trust or store server-side.
    safe_history = [
        {"role": turn.get("role"), "content": turn.get("content")}
        for turn in history[-12:] if isinstance(turn, dict)
    ]
    reply = answer_support_question(message, history=safe_history)
    return jsonify(reply=reply)


# --- Subscription (Paddle, kept live in the backend for any existing sandbox
# subscription - see paddle_webhook below - but no longer linked from the pricing UI,
# which now checks out through Stripe instead. See stripe_billing.py.) ---

def _subscription_response(business_id, hide_nav=False, trial_ended=False):
    business = business_by_id(business_id)
    status = get_subscription_status(business_id)
    return render_template(
        "subscription.html",
        stripe_configured=stripe_billing.is_configured(),
        # Faza 10: two plans (Starter/Pro) x two billing intervals (monthly/yearly) -
        # four distinct Stripe prices, each its own env var. A price whose env var isn't
        # set on Railway yet just shows "Coming soon" on that button - see
        # stripe_billing.price_id_for.
        stripe_prices_configured={
            "starter_monthly": bool(stripe_billing.price_id_for("starter", "monthly")),
            "starter_yearly": bool(stripe_billing.price_id_for("starter", "yearly")),
            "pro_monthly": bool(stripe_billing.price_id_for("pro", "monthly")),
            "pro_yearly": bool(stripe_billing.price_id_for("pro", "yearly")),
        },
        business_id=business_id,
        email=business[4],
        status=status,
        active=effective_subscription_active(business_id),
        pro_access=BETA_FREE_MODE or has_pro_access(business_id),
        has_stripe_billing=bool(get_stripe_customer_id(business_id)),
        hide_nav=hide_nav,
        trial_ended=trial_ended,
    )


@app.route("/subscription")
@login_required
def subscription():
    return _subscription_response(session["business_id"], trial_ended=bool(request.args.get("trial_ended")))


# Mobile-app equivalent of /subscription above ("Choose a plan" on the Subscription
# screen) - same token-bridge pattern as /export-data/<token>, since the mobile app's
# Bearer token can't be attached to a request Capacitor's Browser plugin opens. Hides the
# website nav (hide_nav) since a mobile user has no website session for those links to
# make sense against - see api.py's make_subscription_link_token.
@app.route("/subscription/<token>")
def subscription_mobile(token):
    from api import business_id_from_subscription_link_token  # deferred: avoid circular import at module load

    business_id = business_id_from_subscription_link_token(token)
    if business_id is None:
        abort(404)
    # Bug fix: this route used to render the pricing page for the token's business_id
    # without ever logging the browser tab in - "Choose Pro" then POSTed to /subscription/
    # checkout with no session at all and silently bounced to /login instead of actually
    # starting checkout. The signed, 10-minute token already proves this request came
    # from the mobile app's own Bearer-token-authenticated API call, so it's safe to
    # treat it exactly like a normal login for the rest of this browser tab's session.
    business = business_by_id(business_id)
    if business:
        session["business_id"] = business_id
        session["business_name"] = business[1]
    return _subscription_response(business_id, hide_nav=True)


# --- Subscription (Stripe, Faza 10) ---

@app.route("/subscription/checkout", methods=["POST"])
@login_required
def stripe_checkout():
    business_id = session["business_id"]
    business = business_by_id(business_id)
    plan = request.form.get("plan")
    interval = request.form.get("interval")
    if plan not in ("starter", "pro") or interval not in ("monthly", "yearly"):
        abort(400)

    checkout_url = stripe_billing.create_checkout_session(
        business_id, business[4], plan, interval,
        success_url=url_for("home", _external=True),
        cancel_url=url_for("subscription", _external=True),
    )
    if not checkout_url:
        return redirect(url_for("subscription", checkout_error=1))
    return redirect(checkout_url)


# Self-service billing (update payment method, view invoices, cancel) via Stripe's own
# hosted Customer Portal - so this app never needs to build any of that itself. Only
# available once a business has actually checked out through Stripe at least once.
@app.route("/subscription/manage", methods=["POST"])
@login_required
def stripe_manage_billing():
    stripe_customer_id = get_stripe_customer_id(session["business_id"])
    if not stripe_customer_id:
        return redirect(url_for("subscription"))
    portal_url = stripe_billing.create_billing_portal_session(stripe_customer_id, return_url=url_for("subscription", _external=True))
    if not portal_url:
        return redirect(url_for("subscription", checkout_error=1))
    return redirect(portal_url)


@app.route("/stripe-webhook", methods=["POST"])
@csrf.exempt  # external POST from Stripe, no session/CSRF token of ours - has its own signature check below
def stripe_webhook():
    event = stripe_billing.verify_and_parse_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""))
    if event is None:
        abort(400)

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = data.get("metadata") or {}
        business_id = metadata.get("business_id")
        plan = metadata.get("plan")
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")
        if business_id and subscription_id:
            link_stripe_subscription(int(business_id), customer_id, subscription_id, "active", plan=plan)

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription_id = data.get("id")
        # Stripe's own subscription statuses (active/past_due/canceled/unpaid/...) map
        # directly onto what subscription_active() already checks for - no translation
        # needed, same "trust the processor's own field" approach paddle_webhook uses.
        status = "canceled" if event_type == "customer.subscription.deleted" else data.get("status")
        metadata = data.get("metadata") or {}
        plan = metadata.get("plan")
        if subscription_id:
            update_stripe_subscription_status(subscription_id, status, plan=plan)

    # Stripe expects a 2xx to consider the webhook delivered; otherwise it retries
    return "", 200


def verify_paddle_signature(header, raw_body, secret_key):
    """Verifies the Paddle-Signature header (HMAC-SHA256).

    Header format: 'ts=<timestamp>;h1=<hash>'. The signed payload is
    '<ts>:<raw_body>'. This proves the request really came from Paddle,
    not from someone who guessed the webhook URL and tried to fake a subscription.
    """
    try:
        parts = dict(part.split("=", 1) for part in header.split(";"))
        ts = parts["ts"]
        h1 = parts["h1"]
    except (ValueError, KeyError):
        return False

    signed = ts.encode() + b":" + raw_body
    expected = hmac.new(secret_key.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


@app.route("/paddle-webhook", methods=["POST"])
@csrf.exempt  # external POST from Paddle, no session/CSRF token of ours - already has its own HMAC signature check below
def paddle_webhook():
    secret_key = os.getenv("PADDLE_WEBHOOK_SECRET")
    if not secret_key:
        # Not configured yet - reject rather than silently accepting unsigned requests
        abort(503)

    raw_body = request.get_data()
    if not verify_paddle_signature(request.headers.get("Paddle-Signature", ""), raw_body, secret_key):
        abort(400)

    event = request.get_json(silent=True) or {}
    event_type = event.get("event_type", "")
    data = event.get("data", {})

    # We only care about subscription events (subscription.created/activated/updated/canceled...)
    if event_type.startswith("subscription."):
        sub_id = data.get("id")
        status = data.get("status")
        customer_id = data.get("customer_id")
        # We sent our business_id (and which plan was chosen) through checkout as
        # custom_data - that's how we link the Paddle subscription to our business and
        # know whether it's Starter or Pro (see database.has_pro_access).
        custom_data = data.get("custom_data") or {}
        business_id = custom_data.get("business_id")
        plan = custom_data.get("plan")

        if business_id:
            link_subscription(int(business_id), customer_id, sub_id, status, plan=plan)
        elif sub_id:
            update_subscription_status(sub_id, status, plan=plan)

    # Paddle expects a 2xx to consider the webhook delivered; otherwise it retries
    return "", 200


# --- Instagram DM webhook (replaces the old IMAP polling loop as the message-ingestion
# path - Instagram pushes new messages to us instead of us checking every 60s) ---

@app.route("/instagram-webhook", methods=["GET", "POST"])
@csrf.exempt  # external request from Meta, no session/CSRF token of ours - signed instead (checked below)
def instagram_webhook():
    if request.method == "GET":
        # One-time check Meta does when you first save the webhook URL in the App
        # dashboard - must echo back hub.challenge if hub.verify_token matches.
        try:
            log_webhook_hit("GET verify", str(dict(request.args)))
        except Exception:
            pass
        challenge = verify_webhook_challenge(
            request.args.get("hub.mode"),
            request.args.get("hub.verify_token"),
            request.args.get("hub.challenge"),
        )
        if challenge is None:
            abort(403)
        return challenge, 200

    raw_body = request.get_data()

    # Record the hit into the DB immediately, before anything can fail - this is the
    # ground-truth "did Meta actually call us" signal that doesn't depend on Railway's
    # (unreliable) log viewer. Wrapped so a logging failure can never break the webhook.
    sig_ok = verify_webhook_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""))
    try:
        log_webhook_hit(f"POST sig_ok={sig_ok}", raw_body.decode("utf-8", errors="replace"))
    except Exception:
        pass

    if not sig_ok:
        abort(403)

    payload = request.get_json(silent=True) or {}
    messages = parse_webhook_messages(payload)
    for message in messages:
        try:
            _process_instagram_message(message)
        except Exception as error:
            # One bad message must never break Meta's delivery of the rest of the batch,
            # or cause Meta to keep retrying the whole payload forever.
            print(f"[instagram-webhook] Error processing message: {error!r}", flush=True)

    # Meta expects a fast 2xx - it retries (and can eventually pause the subscription) if
    # this hangs or errors. Processing inline is fine at our current volume (a handful of
    # businesses, and every reply is human-approved anyway, so there's no burst of AI
    # calls to worry about pacing).
    return "", 200


def _process_instagram_message(message):
    business = business_by_instagram_account_id(message["ig_account_id"])
    if business is None:
        print(f"[instagram-webhook] No business found for ig_account_id={message['ig_account_id']!r} - ignoring", flush=True)
        return  # webhook for an Instagram account we don't have on file - shouldn't happen

    business_id = business[0]
    print(f"[instagram-webhook] Matched business_id={business_id} name={business[1]!r}", flush=True)

    # BETA_FREE_MODE means every registered business is processed regardless of billing
    # status (same reasoning as the dashboard banner elsewhere). With it off, a business
    # still gets processed through its own 7-day self-serve trial (subscription_status=
    # 'trialing', set at registration - see add_business()) before needing a real Paddle
    # subscription - see effective_subscription_active()/trial_expired() in database.py.
    if not BETA_FREE_MODE and not effective_subscription_active(business_id):
        print(f"[instagram-webhook] business_id={business_id} subscription not active - skipping", flush=True)
        return
    if replies_today_count(business_id) >= DAILY_REPLY_LIMIT:
        print(f"[instagram-webhook] business_id={business_id} daily limit reached - skipping", flush=True)
        return

    message_id = message["message_id"]
    if message_id and already_processed_messages(business_id, [message_id]):
        print(f"[instagram-webhook] message_id={message_id} already processed - skipping (dedup, flush=True)")
        return  # Meta redelivers on retry - this is what stops a duplicate reply

    sender_igsid = message["sender_igsid"]
    access_token = business[7]
    business_info = {
        "business_name": business[1],
        "working_hours": business[8],
        "price_list": business[9],
        "faq": business[10],
    }
    # Resolved before generating the reply (rather than after, as before) - the AI
    # booking tool below needs a customer name to attach to an appointment it books.
    sender_display = get_sender_username(sender_igsid, access_token) or sender_igsid

    print(f"[instagram-webhook] Generating AI reply for text={message['text']!r} from sender_igsid={sender_igsid}", flush=True)
    history = previous_conversations_with(business_id, sender_igsid)

    # Faza 8/9: Pro-plan (or trialing) businesses get a smarter AI that can check the
    # Schedule for a free slot and book, cancel, or reschedule an appointment directly
    # during the DM conversation - see generate_reply.generate_reply_with_booking/booking.py.
    if BETA_FREE_MODE or has_pro_access(business_id):
        slot_minutes = get_slot_duration(business_id)
        upsell_pairs_text = get_upsell_pairs(business_id)
        now_local_display = booking.now_local().strftime("%A, %Y-%m-%d %H:%M")

        def book_tool(requested_datetime, service=None):
            return booking.attempt_ai_booking(
                business, business_id, slot_minutes, requested_datetime, sender_display, sender_igsid, service, upsell_pairs_text,
            )

        def cancel_tool():
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_igsid, None, upsell_pairs_text,
            )

        def reschedule_tool(new_requested_datetime):
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_igsid, new_requested_datetime, upsell_pairs_text,
            )

        tool_impls = {
            "check_and_book_appointment": book_tool,
            "cancel_appointment": cancel_tool,
            "reschedule_appointment": reschedule_tool,
        }
        suggested_reply = generate_reply_with_booking(message["text"], business_info, history, now_local_display, tool_impls)
    else:
        suggested_reply = generate_reply(message["text"], business_info, history=history)
    print(f"[instagram-webhook] AI reply generated: {suggested_reply!r}", flush=True)
    # Temporary diagnostic (unconditional, not just on the fallback case) - Railway's own
    # logs aren't reachable from here without the Railway CLI, which is blocked on this
    # machine, but /admin/client-errors is, so mirror every generated reply there until
    # the "None" reply bug is confirmed fixed.
    log_client_error(business_id, f"AI reply generated: {suggested_reply!r} for text={message['text']!r}", "", "ai-reply-debug", "")

    new_conversation_id = save_draft_reply(
        business_id, sender_display, message["text"], suggested_reply, sender_igsid=sender_igsid,
    )
    print(f"[instagram-webhook] Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    # Opt-in (default off) - when a business turns this on in Settings, the AI's draft
    # goes straight out instead of waiting for manual approval. Falls back to a normal
    # pending draft if the send itself fails (most likely Instagram's 24h reply window),
    # same as the dashboard's own manual send route.
    auto_sent = False
    if is_auto_send_enabled(business_id):
        try:
            send_instagram_message(sender_igsid, suggested_reply, access_token, business[5])
            mark_sent(new_conversation_id, suggested_reply)
            auto_sent = True
            print(f"[instagram-webhook] Auto-sent reply for conversation_id={new_conversation_id}", flush=True)
        except requests.RequestException as error:
            print(f"[instagram-webhook] Auto-send failed for conversation_id={new_conversation_id}, left as pending: {error}", flush=True)

    try:
        send_push_to_business(
            business_id,
            title="Auto-replied to a customer" if auto_sent else "New customer question",
            body=f"{sender_display}: {message['text'][:80]}",
            data={"conversation_id": str(new_conversation_id)},
        )
    except Exception as error:
        # A push failure must never block draft processing
        print(f"[instagram-webhook] Push notification failed: {error}", flush=True)


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
