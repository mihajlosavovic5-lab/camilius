import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone, date as date_type
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory, Response
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_login_identifier,
    business_by_id, add_business, create_database, delete_business, replies_today_count,
    update_business, business_by_contact_email, save_reset_token, business_by_reset_token, set_new_password,
    link_subscription, update_subscription_status, get_subscription_status,
    link_stripe_subscription, update_stripe_subscription_status, business_by_stripe_subscription_id,
    effective_subscription_active, has_pro_access,
    business_stats, log_event, event_funnel, recent_client_errors, log_client_error,
    add_tester_request, recent_tester_requests,
    link_instagram_account, unlink_instagram_account, business_by_instagram_account_id, already_processed_messages,
    link_whatsapp_account, unlink_whatsapp_account, business_by_whatsapp_phone_number_id,
    link_messenger_page, unlink_messenger_account, business_by_messenger_page_id,
    mark_message_processed, save_draft_reply, previous_conversations_with,
    log_webhook_hit, recent_webhook_hits, raw_conversation_rows, is_auto_send_enabled,
    set_auto_send, business_by_username, business_by_phone_number,
    username_taken, contact_email_taken, phone_number_taken,
    archived_conversations_for_business, trashed_conversations_for_business,
    archive_conversation, unarchive_conversation, soft_delete_conversation, restore_conversation,
    get_slot_duration, set_slot_duration, create_appointment, get_appointment, cancel_appointment,
    is_reminders_enabled, set_reminders_enabled, get_upsell_pairs, set_upsell_pairs,
    is_owner_reminders_enabled, set_owner_reminders_enabled, get_reminder_minutes, set_reminder_minutes,
    all_appointments_for_business, get_stripe_customer_id, get_paddle_customer_id,
    save_email_verify_token, business_by_email_verify_token, mark_email_verified, email_verify_sent_recently,
    email_verify_resend_wait_seconds, save_pending_email_change, confirm_pending_email_change,
    save_phone_verify_token, business_by_phone_verify_token, mark_phone_verified, phone_verify_sent_recently,
    phone_verify_resend_wait_seconds,
    grant_free_pro_access,
    notifications_for_business, unread_notification_count, mark_notifications_read,
    is_new_booking_notifications_enabled, set_new_booking_notifications_enabled,
    record_account_deletion, recent_account_deletions, ACCOUNT_DELETION_REASONS,
    UniqueViolation,
)
from send_email import send_email
from generate_reply import generate_reply, generate_reply_with_booking
from support_assistant import answer_support_question
import booking
import stripe_billing
import paddle_billing
from push_notifications import send_push_to_business
from instagram_api import (
    authorize_url as instagram_authorize_url, exchange_code_for_token, exchange_for_long_lived_token,
    get_account_info, subscribe_account_to_webhook, send_message as send_instagram_message,
    verify_webhook_challenge, verify_webhook_signature, parse_webhook_messages, get_sender_info,
    get_webhook_subscription_status,
)
import whatsapp_api
import messenger_api
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

# Flipped back on (2026-07-26) - no registered business entity yet, so Meta's business
# verification can't be completed and Paddle/Stripe checkout must stay dormant for now;
# recruiting beta/test users instead. All billing code below (Paddle, Stripe) is left in
# place untouched - only this flag changed - so flipping it back to False later (once
# billing can go live for real) restores the exact pricing/checkout behavior described in
# the comment this replaced, no further code changes needed.
BETA_FREE_MODE = True

# Same underlying cause as BETA_FREE_MODE above (no registered business entity yet), but
# a separate flag because it clears on a different milestone: this one turns off the
# moment the Meta app passes App Review, which can happen well before real billing goes
# live. Until then, "Instagram API with Instagram Login" is in Development Mode, where
# only accounts added as Instagram Testers in the Meta App dashboard (App roles > Roles)
# - and who've accepted that invite in Instagram itself (Profile > menu > Settings >
# Apps and websites > Tester invites) - can complete the OAuth screen at all. No API
# exists to add testers or check invite status programmatically, so the only thing this
# flag does is show that requirement on the connect-Instagram screen instead of letting
# people find out the hard way. Flip to False once App Review is approved.
INSTAGRAM_TESTERS_ONLY = True


@app.context_processor
def inject_beta_free_mode():
    return {"beta_free_mode": BETA_FREE_MODE}


@app.context_processor
def inject_instagram_testers_only():
    return {"instagram_testers_only": INSTAGRAM_TESTERS_ONLY}


# Voluntary "support the project" donation link (PayPal.me or similar), shown on the
# subscription page and site footer instead of real billing while BETA_FREE_MODE is on.
# Unset by default so the button/card stay hidden until DONATION_LINK is configured on
# Railway - never renders a dead link.
@app.context_processor
def inject_donation_link():
    return {"donation_link": os.getenv("DONATION_LINK", "").strip() or None}


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
_MODERATE_CACHE_STATIC_PATHS = {
    "/static/manifest.json",
    "/static/icon-192.png",
    "/static/icon-512.png",
    "/static/favicon.ico",
    "/static/favicon-192.png",
} | {
    f"/static/flags/{name}"
    for name in ("gb.svg", "es.png", "pt.svg", "fr.svg", "de.svg", "it.svg", "ru.svg", "rs.png", "cn.svg", "tr.svg")
}
# Werkzeug's plain "no-cache" default (see comment above) only asks a cache to
# revalidate before reusing a stored response - a CDN/proxy sitting between Railway and
# a phone (or the OS's own HTTP cache) is still technically allowed to store one and
# serve it again after a cheap conditional check, which can leave an OTA update
# invisible to the app even though the manifest and zip are both already fresh on the
# origin. These two responses drive the OTA updater directly, so they get the stronger
# "no-store" instead - never keep a copy anywhere, full stop.
_NO_STORE_PATHS = {"/static/mobile-bundle.zip", "/api/mobile-manifest"}


@app.context_processor
def inject_static_versions():
    return {"static_versions": STATIC_VERSIONS}


@app.after_request
def cache_static_assets(response):
    if request.path in _NO_STORE_PATHS:
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    elif request.path in _FOREVER_CACHE_STATIC_PATHS:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path in _MODERATE_CACHE_STATIC_PATHS:
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# Any response rendered for a signed-in visitor (session still has business_id at the
# point this runs, i.e. after the view - a /logout response doesn't count, since that
# view clears the session before returning) must never be storable anywhere - not the
# browser's disk HTTP cache, and per Chrome's own bfcache eligibility rules, a
# Cache-Control: no-store response is excluded from the back/forward cache entirely.
# That's the real fix for "log out, hit back, land back on the account page still
# logged in": the client-side pageshow/htmx:historyRestore handling in base.html reacts
# to a stale page AFTER the browser already restored one from bfcache, but this stops
# that page from ever being eligible for bfcache in the first place - belt and braces,
# since bfcache exclusion behavior isn't identical/guaranteed across every browser.
@app.after_request
def no_store_authenticated_pages(response):
    if session.get("business_id"):
        response.headers["Cache-Control"] = "no-store, private, must-revalidate"
    return response


# Baseline security headers on every response. CSP allows 'unsafe-inline' for script/
# style deliberately - the site relies on many small inline <script> blocks (support
# widget, scroll-to-top, service worker registration, language picker...) throughout
# base.html and would need a broader nonce-based refactor to tighten further; this still
# meaningfully blocks framing (clickjacking), object/embed content, and cross-origin
# fetches, which cost nothing to add now. Stripe Checkout and the old Paddle overlay are
# both same-origin-initiated redirects/scripts, not iframes, so no extra frame-src is needed.
# script-src/connect-src/frame-src all get a *.paddle.com allowance for the pricing
# page's Paddle.js checkout overlay (see templates/subscription.html) - it loads its own
# script from cdn.paddle.com, opens an iframe from a checkout.paddle.com-style origin,
# and that iframe makes its own network calls back to Paddle. Used a wildcard subdomain
# rather than pinning exact hostnames since Paddle's sandbox vs. production checkout
# domains differ and could change - still scoped to Paddle's own infrastructure, not a
# general relaxation. (NOTE: not verified against Paddle's own CSP guidance - their docs
# site blocked automated fetches while building this; if checkout fails to open once real
# PADDLE_PRICE_*/PADDLE_CLIENT_TOKEN are set, check the browser console for a CSP
# violation first and tighten/adjust this list from whatever it names.)
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.paddle.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    # ipapi.co: best-effort IP geolocation the language picker uses to guess a first-time
    # visitor's language from their country (static/site-i18n.js's detectLanguageFromLocation)
    # - without this allowance every one of those requests was silently CSP-blocked, so the
    # feature never actually worked (caught testing this, not by design).
    "connect-src 'self' https://*.paddle.com https://ipapi.co; "
    "frame-src https://*.paddle.com; "
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


# Faza 10: once a logged-in business's 14-day self-serve trial (or paid plan) has
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
            # subscription_status='none' means this account never started a trial at all
            # (real Stripe billing is live and they haven't picked a plan/entered a card
            # yet - see register()'s initial_status) - a "your trial has ended" banner
            # would be a lie, so trial_ended is only passed once there was an actual
            # trial/subscription to lapse from.
            never_started = get_subscription_status(business_id) == "none"
            return redirect(url_for("subscription")) if never_started else redirect(url_for("subscription", trial_ended=1))
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
            if not business[12]:  # email_verified
                if not email_verify_sent_recently(business[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
                    send_verification_email(business[0], business[1], business[4])
                session["pending_verify_business_id"] = business[0]
                return redirect(url_for("verify_pending"))
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

        if request.form.get("password", "") != request.form.get("confirm_password", ""):
            return render_template("register.html", error="Passwords don't match.", data=request.form)

        # Someone who abandoned signup before confirming their email (link buried in
        # spam, app killed in the background while they went looking for it, etc.) has
        # nothing but a dead end otherwise: they can't log in (unverified), "Forgot
        # password" won't help (nothing's wrong with the password), and registering
        # again just says the email is taken. Recognize that case here and resume the
        # existing unverified signup - resend the confirmation link and drop them back
        # into verify_pending - instead of blocking a second time.
        existing = business_by_contact_email(request.form["contact_email"])
        if existing and not existing[12]:  # email_verified
            if not email_verify_sent_recently(existing[0], EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
                send_verification_email(existing[0], existing[1], existing[4])
            session["pending_verify_business_id"] = existing[0]
            return redirect(url_for("verify_pending"))

        # Explicit pre-check rather than relying solely on the UniqueViolation catch below -
        # contact_email has no actual UNIQUE index at the DB level (unlike username/
        # phone_number, which do - see database.py's create_database), a gap that let two
        # businesses register with the identical contact_email and silently broke login
        # (business_by_login_identifier's OR-across-three-columns query started matching
        # both rows, returning whichever one Postgres felt like). This is the real fix
        # until that index exists; see also contact_email_taken's own docstring.
        if existing or contact_email_taken(request.form["contact_email"]):
            return render_template("register.html", error="That username, contact email, or phone number is already taken.", data=request.form)

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
                # Once real Paddle billing is live, a new account no longer gets our own
                # card-free self-serve trial (subscription_status stays 'none' - see
                # add_business) - it lands on /subscription's pricing page instead (via
                # subscription_required, once it reaches a gated page) to pick a plan and
                # start a real, card-backed Paddle trial. Until Paddle is configured this
                # is unchanged from before: every account starts 'trialing' immediately.
                initial_status="none" if paddle_billing.is_configured() else "trialing",
            )
        except UniqueViolation:
            return render_template("register.html", error="That username, contact email, or phone number is already taken.", data=request.form)

        log_event("register_completed", business_id=new_id)
        send_verification_email(new_id, request.form["name"], request.form["contact_email"])
        # Not logged in yet - the account must confirm its email first (see verify_pending()
        # /verify_email() below). connect_instagram() only becomes reachable once that's done.
        session["pending_verify_business_id"] = new_id
        return redirect(url_for("verify_pending"))

    if not session.get("_seen_register"):
        log_event("register_view")
        session["_seen_register"] = True
    return render_template("register.html", error=None, data={})


@app.route("/verify-pending")
def verify_pending():
    business_id = session.get("pending_verify_business_id")
    if not business_id:
        return redirect(url_for("login"))
    business = business_by_id(business_id)
    if business is None or business[12]:  # already verified (or account gone) - nothing pending
        session.pop("pending_verify_business_id", None)
        return redirect(url_for("login"))
    wait_seconds = email_verify_resend_wait_seconds(business_id, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS)
    return render_template("verify_pending.html", contact_email=business[4], resent=False, resend_wait_seconds=wait_seconds)


@app.route("/verify-pending/resend", methods=["POST"])
@limiter.limit("5 per hour")
def verify_pending_resend():
    business_id = session.get("pending_verify_business_id")
    if not business_id:
        return redirect(url_for("login"))
    business = business_by_id(business_id)
    if business is None or business[12]:
        session.pop("pending_verify_business_id", None)
        return redirect(url_for("login"))
    if not email_verify_sent_recently(business_id, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
        send_verification_email(business[0], business[1], business[4])
    wait_seconds = email_verify_resend_wait_seconds(business_id, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS)
    return render_template("verify_pending.html", contact_email=business[4], resent=True, resend_wait_seconds=wait_seconds)


@app.route("/verify-email/<token>")
def verify_email(token):
    row = business_by_email_verify_token(token)
    if row is None:
        return render_template("verify_email.html", success=False)

    business_id, name, _username, _contact_email, pending_contact_email = row

    if pending_contact_email:
        # An already-verified account requested an email change from Settings (see
        # settings() below) rather than confirming its original signup - swap in the
        # staged address instead of the mark_email_verified()/auto-login path below,
        # which is only for that original, one-time signup confirmation.
        try:
            confirm_pending_email_change(business_id)
        except UniqueViolation:
            return render_template("verify_email.html", success=False, email_taken=True)
        log_event("email_changed", business_id=business_id)
        return render_template("verify_email.html", success=True, changed_email=True)

    mark_email_verified(business_id)
    log_event("email_verified", business_id=business_id)
    session.pop("pending_verify_business_id", None)
    session["business_id"] = business_id
    session["business_name"] = name
    return render_template("verify_email.html", success=True)


@app.route("/verify-phone/<token>")
def verify_phone(token):
    row = business_by_phone_verify_token(token)
    if row is None:
        return render_template("verify_phone.html", success=False)

    business_id = row[0]
    mark_phone_verified(business_id)
    log_event("phone_verified", business_id=business_id)
    # Deliberately no session changes here (unlike verify_email above) - this link only
    # ever confirms the phone number itself, it isn't a login credential and shouldn't act
    # like one. Whoever's logged in on this device (if anyone) stays exactly as they were.
    return render_template("verify_phone.html", success=True)


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


# Public (no login required - someone requesting access may not have a Camilius account
# yet, or may have one but obviously can't get past the tester gate to reach a logged-in
# page) hand-off for the INSTAGRAM_TESTERS_ONLY period: collects an Instagram username so
# the founder can add it in the Meta App dashboard (App roles > Roles > Instagram
# Testers) without having to chase people down for it over DM. Linked from
# connectInstagram.testerNote's "haven't been invited yet?" line.
@app.route("/request-tester-access", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def request_tester_access():
    if request.method == "POST":
        instagram_username = request.form.get("instagram_username", "").strip().lstrip("@")
        email = request.form.get("email", "").strip()
        note = request.form.get("note", "").strip() or None

        if not instagram_username or not email:
            return render_template(
                "request_tester_access.html", submitted=False,
                error="Please fill in your Instagram username and email.", data=request.form,
            )

        add_tester_request(instagram_username, email, note)
        log_event("tester_access_requested")

        # Best-effort only, same as every other notification-style send in this file - a
        # failure here must not lose the request itself, which is already safely in the
        # DB by this point (visible at /admin/tester-requests regardless).
        alert_email = os.getenv("ALERT_EMAIL")
        if alert_email:
            try:
                send_email(
                    recipient=alert_email,
                    subject=f"New tester request: @{instagram_username}",
                    body=(
                        f"Instagram: @{instagram_username}\n"
                        f"Email: {email}\n"
                        f"Note: {note or '(none)'}\n\n"
                        f"Add them: Meta App dashboard > App roles > Roles > Instagram Testers."
                    ),
                    business_name="Camilius",
                    reply_to=email,
                )
            except Exception as error:
                log_client_error(None, f"tester request notification email failed: {error}", "", "tester-request-notify", "")

        return render_template("request_tester_access.html", submitted=True)

    return render_template("request_tester_access.html", submitted=False, error=None, data={})


@app.route("/connect-messenger")
@login_required
def connect_messenger():
    business = business_by_id(session["business_id"])
    if business[18]:  # messenger_page_id already set - nothing to do
        return redirect(url_for("home"))
    return render_template("connect_messenger.html", error=request.args.get("error"))


@app.route("/connect-messenger/start")
@login_required
def connect_messenger_start():
    redirect_uri = url_for("messenger_oauth_callback", _external=True)
    return redirect(messenger_api.authorize_url(redirect_uri))


@app.route("/messenger-oauth-callback")
@login_required
def messenger_oauth_callback():
    """Web-only for now (unlike Instagram, no mobile "state" flow yet - a reasonable next
    increment, not built this round). Has an extra step Instagram's callback doesn't need:
    a business can manage more than one Facebook Page, so this branches three ways below
    instead of connecting immediately."""
    code = request.args.get("code")
    if request.args.get("error") or not code:
        return redirect(url_for("connect_messenger", error="Messenger connection was cancelled. Try again whenever you're ready."))

    redirect_uri = url_for("messenger_oauth_callback", _external=True)
    try:
        short_lived = messenger_api.exchange_code_for_token(code, redirect_uri)
        long_lived_token = messenger_api.exchange_for_long_lived_token(short_lived)
        pages = messenger_api.get_pages(long_lived_token)
    except requests.RequestException as error:
        detail = error.response.text if getattr(error, "response", None) is not None else str(error)
        log_client_error(session["business_id"], f"messenger connect failed: {detail}", "", "messenger-connect-debug", "")
        return redirect(url_for("connect_messenger", error="Couldn't connect to Facebook - please try again."))

    if not pages:
        return redirect(url_for("connect_messenger", error="No Facebook Page found for your account - create one first, then try again."))

    if len(pages) == 1:
        return _finish_connecting_messenger_page(pages[0])

    # More than one Page - ask which one. Stashed in the session (small, signed cookie;
    # a small business realistically manages a handful of Pages at most) rather than a DB
    # table, purely to bridge this callback and the picker's own POST below - nothing
    # here needs to outlive that.
    session["messenger_pending_pages"] = [{"id": p["id"], "name": p["name"], "access_token": p["access_token"]} for p in pages]
    return render_template("select_messenger_page.html", pages=pages, error=None)


@app.route("/connect-messenger/select-page", methods=["POST"])
@login_required
def connect_messenger_select_page():
    pending_pages = session.get("messenger_pending_pages") or []
    chosen_id = request.form.get("page_id")
    chosen = next((p for p in pending_pages if p["id"] == chosen_id), None)
    if not chosen:
        return redirect(url_for("connect_messenger", error="That Page selection expired - please reconnect."))
    return _finish_connecting_messenger_page(chosen)


def _finish_connecting_messenger_page(page):
    session.pop("messenger_pending_pages", None)
    try:
        messenger_api.subscribe_page_to_webhook(page["id"], page["access_token"])
    except requests.RequestException as error:
        detail = error.response.text if getattr(error, "response", None) is not None else str(error)
        log_client_error(session["business_id"], f"messenger connect failed: {detail}", "", "messenger-connect-debug", "")
        return redirect(url_for("connect_messenger", error="Couldn't connect to Facebook - please try again."))

    try:
        link_messenger_page(session["business_id"], page["id"], page["name"], page["access_token"])
    except UniqueViolation:
        return redirect(url_for("connect_messenger", error="This Facebook Page is already connected to another Camilius account."))

    log_event("messenger_connected", business_id=session["business_id"])
    return redirect(url_for("home"))


@app.route("/disconnect-messenger", methods=["POST"])
@login_required
def disconnect_messenger():
    unlink_messenger_account(session["business_id"])
    return redirect(url_for("settings", message="Messenger disconnected."))


@app.route("/instagram-oauth-callback")
def instagram_oauth_callback():
    # Two ways to reach this route: the website flow (logged-in session cookie), and the
    # mobile app flow (no cookie at all - the app only ever holds a Bearer token, never a
    # website session). The mobile app instead identifies itself via the OAuth "state"
    # param, set to a short-lived signed token when it requested the authorize URL from
    # /api/instagram-connect-url - Meta echoes "state" back unmodified on this callback,
    # which is exactly what that parameter is for.
    from api import business_id_from_instagram_connect_state, mark_instagram_connect_state_done  # deferred: avoid circular import at module load

    state = request.args.get("state")
    mobile_business_id = business_id_from_instagram_connect_state(state)
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
            mark_instagram_connect_state_done(state, business_id)
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
    except requests.RequestException as error:
        # The user only ever sees the generic message below (Meta's actual error detail
        # isn't something a business owner can act on) - logged here instead so it's
        # visible in /admin/client-errors, since this was previously a total black box
        # when a business hit it more than once in a row.
        detail = error.response.text if getattr(error, "response", None) is not None else str(error)
        log_client_error(business_id, f"instagram connect failed: {detail}", "", "instagram-connect-debug", "")
        if is_mobile_flow:
            mark_instagram_connect_state_done(state, business_id)
            return render_template("instagram_connect_mobile.html", success=False)
        return redirect(url_for("connect_instagram", error="Couldn't connect to Instagram - please try again."))
    except UniqueViolation:
        # businesses.instagram_account_id has a UNIQUE index (see database.py) - this
        # Instagram account is already linked to a different Camilius business (e.g. an
        # older test/registration of the same person's account), so the UPDATE inside
        # link_instagram_account() was rejected at the DB level rather than raising a
        # requests.RequestException like every other failure above. Previously uncaught,
        # this fell straight through to the generic Flask 500 page instead of telling the
        # business owner what actually went wrong.
        log_client_error(business_id, "instagram connect failed: instagram_account_id already linked to another business", "", "instagram-connect-debug", "")
        if is_mobile_flow:
            mark_instagram_connect_state_done(state, business_id)
            return render_template("instagram_connect_mobile.html", success=False)
        message = "This Instagram account is already connected to another Camilius account. Disconnect it there first, or use a different Instagram account."
        return redirect(url_for("connect_instagram", error=message))

    log_event("instagram_connected", business_id=business_id)
    if is_mobile_flow:
        mark_instagram_connect_state_done(state, business_id)
        return render_template("instagram_connect_mobile.html", success=True)
    return redirect(url_for("home"))


def _password_reset_email_html(business_name, username, link):
    """HTML companion to the plain-text body below - same copy, laid out as a branded
    card (logo, amber CTA button, muted footer) so the reset email reads like the rest
    of the product instead of a bare-bones system notice."""
    logo_url = url_for("static", filename="icon-512.png", _external=True)
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#f6f1e7; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f1e7; padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%; background:#ffffff; border-radius:14px; overflow:hidden; box-shadow:0 4px 24px rgba(43,32,19,.08);">
          <tr>
            <td style="padding:32px 40px 8px; text-align:center;">
              <img src="{logo_url}" width="48" height="48" alt="Camilius" style="border-radius:22%; display:block; margin:0 auto 12px;">
              <div style="font-size:19px; font-weight:700; color:#2b2013;">Camilius</div>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 40px 0;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:16px 0;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px; text-align:center;">
              <h1 style="font-size:20px; color:#2b2013; margin:0 0 16px;">Reset your password</h1>
              <p style="font-size:15px; line-height:1.6; color:#463824; margin:0 0 16px;">
                Someone (hopefully you) requested a password reset for <strong>{business_name}</strong>.
              </p>
              <p style="font-size:15px; line-height:1.6; color:#463824; margin:0 0 24px;">
                Your username is <strong>{username}</strong>.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td style="border-radius:999px; background:#d69410;">
                    <a href="{link}" style="display:inline-block; padding:13px 32px; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; border-radius:999px;">Set a new password</a>
                  </td>
                </tr>
              </table>
              <p style="font-size:13.5px; line-height:1.6; color:#8a7a5f; margin:0 0 24px;">
                This link expires in 1 hour. If the button above doesn't work, copy and paste this URL into your browser:<br>
                <a href="{link}" style="color:#9a6a08; word-break:break-all;">{link}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px 32px; text-align:center;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:0 0 20px;">
              <p style="font-size:13px; line-height:1.6; color:#8a7a5f; margin:0;">
                If you didn't request this, you can safely ignore this email — your password won't change.
              </p>
            </td>
          </tr>
        </table>
        <p style="font-size:12px; color:#a6987d; margin:20px 0 0;">&copy; {datetime.now(timezone.utc).year} Camilius. All rights reserved.</p>
      </td>
    </tr>
  </table>
</body>
</html>"""


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
        html=_password_reset_email_html(business[1], business[2], link),
    )


EMAIL_VERIFY_TOKEN_LIFETIME = timedelta(hours=24)
# Minimum gap between two verification emails to the same account - stops the login
# form (which re-triggers a resend on every attempt while unverified, see login() below)
# from firing a fresh email on every single failed/retried submit.
EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS = 60

# Same idea as EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS, for the "Send reset link"/"Find
# account" buttons on the forgot-password screen - there's no per-account sent-at column
# for this one (no throttled resend endpoint to hit), so it's purely a client-side cooldown
# the forgot_password.html/forgot_password_phone() success renders seed the countdown with.
FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS = 60


def _verification_email_html(heading, intro_html, link, footer_note):
    """HTML companion to the plain-text bodies below - same branded-card layout as
    _password_reset_email_html, shared by both the signup confirmation link
    (send_verification_email) and the email-change confirmation link
    (send_email_change_verification), which only differ in copy."""
    logo_url = url_for("static", filename="icon-512.png", _external=True)
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#f6f1e7; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f1e7; padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%; background:#ffffff; border-radius:14px; overflow:hidden; box-shadow:0 4px 24px rgba(43,32,19,.08);">
          <tr>
            <td style="padding:32px 40px 8px; text-align:center;">
              <img src="{logo_url}" width="48" height="48" alt="Camilius" style="border-radius:22%; display:block; margin:0 auto 12px;">
              <div style="font-size:19px; font-weight:700; color:#2b2013;">Camilius</div>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 40px 0;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:16px 0;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px;">
              <h1 style="font-size:20px; color:#2b2013; margin:0 0 16px;">{heading}</h1>
              <p style="font-size:15px; line-height:1.6; color:#463824; margin:0 0 24px;">
                {intro_html}
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="border-radius:999px; background:#d69410;">
                    <a href="{link}" style="display:inline-block; padding:13px 32px; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; border-radius:999px;">Confirm email</a>
                  </td>
                </tr>
              </table>
              <p style="font-size:13.5px; line-height:1.6; color:#8a7a5f; margin:0 0 24px;">
                This link expires in 24 hours. If the button above doesn't work, copy and paste this URL into your browser:<br>
                <a href="{link}" style="color:#9a6a08; word-break:break-all;">{link}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px 32px;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:0 0 20px;">
              <p style="font-size:13px; line-height:1.6; color:#8a7a5f; margin:0;">
                {footer_note}
              </p>
            </td>
          </tr>
        </table>
        <p style="font-size:12px; color:#a6987d; margin:20px 0 0;">&copy; {datetime.now(timezone.utc).year} Camilius. All rights reserved.</p>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_verification_email(business_id, business_name, recipient):
    """Issues a fresh 24h single-use token and emails the confirmation link. Shared by the
    web /register route, the mobile /api/register endpoint, and both places that resend it
    (login() below and /api/login) once an account already exists but hasn't confirmed yet.

    The token is saved and the account already exists in the DB before this is ever called
    - a broken send (Resend down, bad API key, network blip) must not turn into a 500 that
    leaves the business stuck (account created, no session, nothing to resend from). Caught
    and logged here instead: the caller's own redirect to verify_pending still happens, and
    that page's own "Resend" button (see /verify-pending/resend) covers a retry once
    whatever went wrong clears."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + EMAIL_VERIFY_TOKEN_LIFETIME
    save_email_verify_token(business_id, token, expires)

    link = url_for("verify_email", token=token, _external=True)
    try:
        send_email(
            recipient=recipient,
            subject="Confirm your email - Camilius",
            body=(
                f"Hi,\n\n"
                f"Welcome to Camilius! Confirm '{business_name}'s email address to finish setting up your account.\n\n"
                f"Click the link to confirm (valid for 24 hours):\n{link}\n\n"
                f"If you didn't create a Camilius account, you can safely ignore this email."
            ),
            business_name="Camilius",
            reply_to=os.getenv("ALERT_EMAIL") or recipient,
            html=_verification_email_html(
                heading="Confirm your email",
                intro_html=f"Welcome to Camilius! Confirm <strong>{business_name}</strong>'s email address to finish setting up your account.",
                link=link,
                footer_note="If you didn't create a Camilius account, you can safely ignore this email.",
            ),
        )
    except Exception as error:
        log_client_error(business_id, f"verification email send failed: {error}", "", "send-verification-email", "")


def send_email_change_verification(business_id, business_name, new_email):
    """Sent to the NEW address when an already-verified account requests an email change
    from the web Settings page (settings()) - the account's current, still-verified email
    keeps working until this link is clicked. Confirming it swaps contact_email for
    new_email (see verify_email()/confirm_pending_email_change()); the account's
    email_verified flag is untouched since it was already TRUE before the request."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + EMAIL_VERIFY_TOKEN_LIFETIME
    save_pending_email_change(business_id, new_email, token, expires)

    link = url_for("verify_email", token=token, _external=True)
    send_email(
        recipient=new_email,
        subject="Confirm your new email - Camilius",
        body=(
            f"Hi,\n\n"
            f"'{business_name}' on Camilius asked to change its contact email to this address.\n\n"
            f"Click the link to confirm the change (valid for 24 hours):\n{link}\n\n"
            f"If you didn't request this, you can safely ignore this email - your current email stays unchanged."
        ),
        business_name="Camilius",
        reply_to=os.getenv("ALERT_EMAIL") or new_email,
        html=_verification_email_html(
            heading="Confirm your new email",
            intro_html=f"<strong>{business_name}</strong> on Camilius asked to change its contact email to this address.",
            link=link,
            footer_note="If you didn't request this, you can safely ignore this email - your current email stays unchanged.",
        ),
    )


PHONE_VERIFY_TOKEN_LIFETIME = timedelta(hours=24)
PHONE_VERIFY_RESEND_COOLDOWN_SECONDS = 60


def _phone_verification_email_html(business_name, phone_number, link):
    """Same branded-card layout as _verification_email_html - there's no SMS integration
    (see .env.example's own note on that), so phone verification is confirmed by email
    too, exactly like the account email itself."""
    logo_url = url_for("static", filename="icon-512.png", _external=True)
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#f6f1e7; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f1e7; padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px; width:100%; background:#ffffff; border-radius:14px; overflow:hidden; box-shadow:0 4px 24px rgba(43,32,19,.08);">
          <tr>
            <td style="padding:32px 40px 8px; text-align:center;">
              <img src="{logo_url}" width="48" height="48" alt="Camilius" style="border-radius:22%; display:block; margin:0 auto 12px;">
              <div style="font-size:19px; font-weight:700; color:#2b2013;">Camilius</div>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 40px 0;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:16px 0;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px;">
              <h1 style="font-size:20px; color:#2b2013; margin:0 0 16px;">Confirm your phone number</h1>
              <p style="font-size:15px; line-height:1.6; color:#463824; margin:0 0 24px;">
                Confirm <strong>{business_name}</strong>'s phone number (<strong>{phone_number}</strong>) so you can use it to log in later, in addition to your username and email.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="border-radius:999px; background:#d69410;">
                    <a href="{link}" style="display:inline-block; padding:13px 32px; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; border-radius:999px;">Confirm phone number</a>
                  </td>
                </tr>
              </table>
              <p style="font-size:13.5px; line-height:1.6; color:#8a7a5f; margin:0 0 24px;">
                This link expires in 24 hours. If the button above doesn't work, copy and paste this URL into your browser:<br>
                <a href="{link}" style="color:#9a6a08; word-break:break-all;">{link}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px 32px;">
              <hr style="border:none; border-top:1px solid #eee2cc; margin:0 0 20px;">
              <p style="font-size:13px; line-height:1.6; color:#8a7a5f; margin:0;">
                If you didn't request this, you can safely ignore this email - your account is unaffected either way.
              </p>
            </td>
          </tr>
        </table>
        <p style="font-size:12px; color:#a6987d; margin:20px 0 0;">&copy; {datetime.now(timezone.utc).year} Camilius. All rights reserved.</p>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_phone_verification_email(business_id, business_name, phone_number, recipient):
    """Issues a fresh 24h single-use token and emails the confirmation link to the
    account's contact email (there's no SMS channel - see .env.example). Triggered only
    from the Profile screen's "Verify phone number" button (see api.py's
    /api/verify-phone/send), never automatically at registration - unlike the account
    email, an unconfirmed phone just can't be used to log in yet, it doesn't block
    anything else."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + PHONE_VERIFY_TOKEN_LIFETIME
    save_phone_verify_token(business_id, token, expires)

    link = url_for("verify_phone", token=token, _external=True)
    send_email(
        recipient=recipient,
        subject="Confirm your phone number - Camilius",
        body=(
            f"Hi,\n\n"
            f"Confirm '{business_name}'s phone number ({phone_number}) so you can use it to log in later, "
            f"in addition to your username and email.\n\n"
            f"Click the link to confirm (valid for 24 hours):\n{link}\n\n"
            f"If you didn't request this, you can safely ignore this email - your account is unaffected either way."
        ),
        business_name="Camilius",
        reply_to=os.getenv("ALERT_EMAIL") or recipient,
        html=_phone_verification_email_html(business_name, phone_number, link),
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
            resend_wait_seconds=FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS,
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
        phone_mode=True,
        resend_wait_seconds=FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS,
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
    field = data.get("field")
    check = _AVAILABILITY_CHECKS.get(field)
    if check is None:
        abort(400)
    if not value:
        return jsonify(taken=False)

    business_id = session.get("business_id")
    if field == "contact_email" and business_id is None:
        # Anonymous caller, i.e. the Create Account wizard (Settings' email field always
        # has a logged-in session - see the branch below) - an email stuck on an
        # abandoned, never-verified signup isn't a real conflict here, it's exactly the
        # account register() will resume instead of blocking (see register()'s own
        # comment above). Only a genuinely confirmed account should hold this field
        # "taken" and keep the wizard's Next button disabled.
        existing = business_by_contact_email(value)
        return jsonify(taken=bool(existing and existing[12]))

    return jsonify(taken=check(value, exclude_business_id=business_id))


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
    # from_logout=1 tells login.html to arm the back-button trap below - only right
    # after an actual logout, not on a normal visit to /login, where back should behave
    # normally (e.g. returning to wherever "Log in" was clicked from).
    return redirect(url_for("login", from_logout=1))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def settings():
    business_id = session["business_id"]
    business = business_by_id(business_id)
    # indices: 0 id, 1 name, 2 username, 4 contact_email, 5 instagram_account_id,
    # 6 instagram_username, 8 working_hours, 9 price_list, 10 faq, 11 phone_number,
    # 14 pending_contact_email, 16 whatsapp_display_number, 19 messenger_page_name
    username = business[2]
    contact_email = business[4]
    instagram_username = business[6]
    pending_contact_email = business[14]
    whatsapp_display_number = business[16]
    messenger_page_name = business[19]
    pro_access = BETA_FREE_MODE or has_pro_access(business_id)

    if request.method == "POST":
        name = request.form["name"]
        submitted_username = request.form["username"].strip()
        submitted_email = request.form["contact_email"].strip()
        working_hours = request.form["working_hours"]
        price_list = request.form["price_list"]
        faq = request.form["faq"]
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")

        def _rerender(error):
            return render_template(
                "settings.html",
                error=error,
                message=None,
                pro_access=pro_access,
                business={
                    "name": name, "username": username, "contact_email": contact_email,
                    "pending_contact_email": pending_contact_email, "instagram_username": instagram_username,
                    "whatsapp_display_number": whatsapp_display_number, "messenger_page_name": messenger_page_name,
                    "working_hours": working_hours, "price_list": price_list, "faq": faq,
                    "upsell_pairs": get_upsell_pairs(business_id),
                },
            )

        if not submitted_username:
            return _rerender("Username is required.")
        if not submitted_email:
            return _rerender("Contact email is required.")

        # Username applies immediately, unlike contact email below - it isn't a
        # communication channel that could strand the account if mistyped, so there's
        # nothing to confirm. settings.html's own JS already warns about a taken username
        # as the user types; this is the authoritative check the client-side one mirrors.
        if submitted_username != username:
            if username_taken(submitted_username, exclude_business_id=business_id):
                return _rerender("That username is already taken.")
            username = submitted_username

        # A changed email doesn't take effect immediately - it's staged as
        # pending_contact_email and only swapped onto the real contact_email once a
        # confirmation link sent to the NEW address is clicked (see verify_email() and
        # send_email_change_verification()). The account keeps working under its current,
        # already-verified address in the meantime, so a mistyped or never-checked new
        # address can't lock anyone out - unlike phone_number's apply-then-verify pattern
        # in update_business(), which is fine there since an unconfirmed phone only blocks
        # phone-based login, never the whole account.
        if submitted_email != contact_email:
            if contact_email_taken(submitted_email, exclude_business_id=business_id):
                return _rerender("That contact email is already taken.")
            if not email_verify_sent_recently(business_id, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS):
                send_email_change_verification(business_id, name, submitted_email)
            pending_contact_email = submitted_email

        # Password change - same "current password required, new one at least 8 chars"
        # rule as the mobile app's own Profile screen (see api.py's api_update_settings).
        # An empty new_password field means the user isn't touching their password at all.
        if new_password:
            if not check_password_hash(business[3], current_password):
                return _rerender("Current password is incorrect.")
            if len(new_password) < 8:
                return _rerender("New password must be at least 8 characters.")

        # Phone isn't editable from this page (the mobile app's Profile screen is where
        # that lives) - carry the existing value through unchanged.
        update_business(business_id, name, username, contact_email, business[11], working_hours, price_list, faq)
        session["business_name"] = name

        if new_password:
            set_new_password(business_id, new_password)

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
                "name": name, "username": username, "contact_email": contact_email,
                "pending_contact_email": pending_contact_email, "instagram_username": instagram_username,
                "whatsapp_display_number": whatsapp_display_number, "messenger_page_name": messenger_page_name,
                "working_hours": working_hours, "price_list": price_list, "faq": faq, "upsell_pairs": upsell_pairs,
            },
        )

    return render_template(
        "settings.html",
        error=None,
        message=request.args.get("message"),
        pro_access=pro_access,
        business={
            "name": business[1], "username": username, "contact_email": contact_email,
            "pending_contact_email": pending_contact_email, "instagram_username": instagram_username,
            "whatsapp_display_number": whatsapp_display_number, "messenger_page_name": messenger_page_name,
            "working_hours": business[8], "price_list": business[9], "faq": business[10],
            "upsell_pairs": get_upsell_pairs(business_id),
        },
    )


@app.route("/disconnect-instagram", methods=["POST"])
@login_required
def disconnect_instagram():
    # Frees up instagram_account_id's UNIQUE index (see database.py) so the account can
    # be reconnected here or linked to a different business - see instagram_oauth_callback's
    # UniqueViolation handling above for the failure this exists to avoid.
    unlink_instagram_account(session["business_id"])
    return redirect(url_for("settings", message="Instagram disconnected."))


@app.route("/connect-whatsapp", methods=["GET", "POST"])
@login_required
def connect_whatsapp():
    """No OAuth redirect here, unlike Instagram - see whatsapp_api.py's module docstring
    for why. The business pastes in values it generated itself, directly in its own Meta
    Business Suite: a Phone Number ID and WhatsApp Business Account (WABA) ID (both shown
    on the WABA's own dashboard page), a display number (just a label, doesn't need to be
    exact), and a permanent access token (System User > Generate token, with that WABA's
    messaging permission, scoped to Camilius's Meta App as a partner)."""
    if request.method == "POST":
        phone_number_id = request.form.get("phone_number_id", "").strip()
        waba_id = request.form.get("waba_id", "").strip()
        display_number = request.form.get("display_number", "").strip()
        access_token = request.form.get("access_token", "").strip()

        if not phone_number_id or not waba_id or not display_number or not access_token:
            return render_template("connect_whatsapp.html", error="Please fill in every field.")

        # Subscribing the WABA to Camilius's webhook doubles as validating the pasted
        # token actually works - saving unusable credentials (typo'd token, wrong
        # permission scope) would otherwise look "connected" while silently never
        # receiving a single message.
        try:
            whatsapp_api.subscribe_account_to_webhook(waba_id, access_token)
        except requests.RequestException as error:
            detail = error.response.text if getattr(error, "response", None) is not None else str(error)
            log_client_error(session["business_id"], f"whatsapp connect failed: {detail}", "", "whatsapp-connect-debug", "")
            return render_template("connect_whatsapp.html", error="Couldn't verify that - double check the IDs and token, then try again.")

        try:
            link_whatsapp_account(session["business_id"], phone_number_id, display_number, access_token)
        except UniqueViolation:
            return render_template("connect_whatsapp.html", error="This WhatsApp number is already connected to another Camilius account.")

        log_event("whatsapp_connected", business_id=session["business_id"])
        return redirect(url_for("settings", message="WhatsApp connected."))

    return render_template("connect_whatsapp.html", error=None)


@app.route("/disconnect-whatsapp", methods=["POST"])
@login_required
def disconnect_whatsapp():
    unlink_whatsapp_account(session["business_id"])
    return redirect(url_for("settings", message="WhatsApp disconnected."))


# Distinct from /settings above (renamed "Profile" in the nav - business info the AI
# uses) - this is app-level preferences: legal, help, and account-data actions, mirroring
# the mobile app's own Settings screen split.
# Preset lead-time choices offered by the pill picker on each reminder toggle (see
# app_settings.html) - minutes before the appointment. A business can also type any
# other value via the picker's "Custom" option (see _parse_reminder_minutes below);
# REMINDER_LEAD_MAX_MINUTES caps that input and matches the lookahead window
# database.appointments_needing_reminders queries.
REMINDER_LEAD_CHOICES = [(15, "15 min"), (30, "30 min"), (60, "1h"), (120, "2h"), (1440, "24h")]
REMINDER_LEAD_MAX_MINUTES = 7 * 24 * 60


def _duration_choices_with_custom(choices, current_value, label_fn=str):
    """Merges a saved custom value into a preset (value, label) choice list, sorted
    ascending by value - used by every pill picker that offers a fixed set of presets
    plus a "Custom" option (reminder lead times, slot duration), so a custom pick reads
    in its correct smallest-to-largest position (e.g. 5 min before 15 min) instead of
    always being tacked on after the largest preset just because it was chosen last."""
    pairs = list(choices)
    if current_value not in {value for value, _ in pairs}:
        pairs.append((current_value, label_fn(current_value)))
    return sorted(pairs, key=lambda pair: pair[0])


@app.route("/settings")
@login_required
def app_settings():
    business_id = session["business_id"]
    customer_minutes, owner_minutes = get_reminder_minutes(business_id)
    customer_choices = _duration_choices_with_custom(REMINDER_LEAD_CHOICES, customer_minutes, lambda m: f"{m} min")
    owner_choices = _duration_choices_with_custom(REMINDER_LEAD_CHOICES, owner_minutes, lambda m: f"{m} min")
    return render_template(
        "app_settings.html",
        auto_send=is_auto_send_enabled(business_id),
        new_booking_notifications_enabled=is_new_booking_notifications_enabled(business_id),
        reminders_enabled=is_reminders_enabled(business_id),
        owner_reminders_enabled=is_owner_reminders_enabled(business_id),
        customer_reminder_minutes=customer_minutes,
        owner_reminder_minutes=owner_minutes,
        customer_reminder_choices=customer_choices,
        owner_reminder_choices=owner_choices,
        customer_reminder_label=dict(customer_choices)[customer_minutes],
        owner_reminder_label=dict(owner_choices)[owner_minutes],
        reminder_lead_minutes=[value for value, _ in REMINDER_LEAD_CHOICES],
        reminder_lead_max_minutes=REMINDER_LEAD_MAX_MINUTES,
        pro_access=BETA_FREE_MODE or has_pro_access(business_id),
    )


# "New bookings" toggle - unconditional, no Pro gate, unlike the reminders toggles
# below. Website equivalent of the mobile app's new_booking_notifications_enabled field
# (see api.py's /api/notification-preferences). No standalone master on/off switch here
# (dropped - a single broad "Notifications" toggle on top of these per-type ones read as
# redundant/confusing rather than useful).
@app.route("/settings/notifications", methods=["POST"])
@login_required
def update_notifications():
    business_id = session["business_id"]
    set_new_booking_notifications_enabled(business_id, bool(request.form.get("new_booking_notifications_enabled")))
    return redirect(url_for("app_settings"))


# Website equivalent of the mobile app's Auto-send toggle (see api.py's
# /api/notification-preferences) - same underlying businesses.auto_send column, just
# session-authenticated instead of Bearer-token, since the website never had a way to
# change this at all before now. The checkbox itself is framed as opt-in "AI Draft Mode"
# (auto-send is already the default), so it posts the inverse of the stored column.
@app.route("/settings/auto-send", methods=["POST"])
@login_required
def update_auto_send():
    set_auto_send(session["business_id"], not bool(request.form.get("draft_mode")))
    return redirect(url_for("app_settings"))


def _parse_reminder_minutes(picker_value, custom_value, default):
    """picker_value is either one of REMINDER_LEAD_CHOICES' minute values (as a string)
    or the literal "custom", in which case custom_value (the picker's own number input)
    is what actually gets used. Falls back to `default` on anything malformed rather than
    rejecting the whole form - this is a convenience preference, not something worth a
    validation error page over."""
    raw = custom_value if picker_value == "custom" else picker_value
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return default
    return minutes if 1 <= minutes <= REMINDER_LEAD_MAX_MINUTES else default


# Appointment reminders toggle (Faza 9/12, Pro-only) - Starter-plan businesses can't turn
# either one on even via a raw POST; both toggles are locked/hidden for them in
# app_settings.html. Customer and owner reminders are independent: either, both, or
# neither can be on, each with its own lead time.
@app.route("/settings/reminders", methods=["POST"])
@login_required
def update_reminders():
    business_id = session["business_id"]
    if BETA_FREE_MODE or has_pro_access(business_id):
        set_reminders_enabled(business_id, bool(request.form.get("reminders_enabled")))
        set_owner_reminders_enabled(business_id, bool(request.form.get("owner_reminders_enabled")))
        customer_minutes = _parse_reminder_minutes(
            request.form.get("customer_reminder_minutes"), request.form.get("customer_reminder_minutes_custom"), 1440)
        owner_minutes = _parse_reminder_minutes(
            request.form.get("owner_reminder_minutes"), request.form.get("owner_reminder_minutes_custom"), 60)
        set_reminder_minutes(business_id, customer_minutes, owner_minutes)
    return redirect(url_for("app_settings"))


# Bell icon (base.html, shown on every logged-in page) - owner appointment reminders
# (reminders.py) land here instead of an email, so this is polled once per page load the
# same way the mobile app's /api/notifications is. Session-authed web equivalent of that
# Bearer-authed endpoint - same underlying notifications table either way.
@app.route("/notifications")
@login_required
def notifications():
    business_id = session["business_id"]
    rows = notifications_for_business(business_id)
    return jsonify(
        notifications=[
            {
                "id": row[0], "title": row[1], "body": row[2], "kind": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
                "read": row[5] is not None,
            }
            for row in rows
        ],
        unread_count=unread_notification_count(business_id),
    )


@app.route("/notifications/read", methods=["POST"])
@login_required
def mark_notifications_read_route():
    data = request.get_json(silent=True) or {}
    mark_notifications_read(session["business_id"], data.get("id"))
    return jsonify(message="saved", unread_count=unread_notification_count(session["business_id"]))


# --- Delete-account wizard (web) - three separate pages instead of one instant-delete
# button, mirroring the mobile app's own screen-delete-warning/reason/confirm flow (see
# mobile/app.js). Wizard state is carried forward purely via each step's own hidden form
# fields (no server-side session stash needed) - step 3 is rendered by POSTing step 2's
# form straight into it, and step 3's own form re-submits everything (reason included)
# to the final POST /delete-account below.

@app.route("/settings/delete-account")
@login_required
def delete_account_step1():
    return render_template("delete_account_step1.html")


@app.route("/settings/delete-account/reason")
@login_required
def delete_account_step2():
    return render_template("delete_account_step2.html", reason_category=None, reason_details="", error=None)


@app.route("/settings/delete-account/confirm", methods=["POST"])
@login_required
def delete_account_step3():
    reason_category = request.form.get("reason_category", "")
    reason_details = (request.form.get("reason_details") or "").strip()[:1000]
    if reason_category not in ACCOUNT_DELETION_REASONS:
        return render_template(
            "delete_account_step2.html", reason_category=reason_category, reason_details=reason_details,
            error="Please choose a reason so we know why you're leaving.",
        )
    return render_template(
        "delete_account_step3.html", reason_category=reason_category, reason_details=reason_details, error=None,
    )


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    business_id = session["business_id"]
    reason_category = request.form.get("reason_category", "")
    reason_details = (request.form.get("reason_details") or "").strip()[:1000]
    password = request.form.get("password", "")

    if reason_category not in ACCOUNT_DELETION_REASONS:
        return render_template(
            "delete_account_step2.html", reason_category=reason_category, reason_details=reason_details,
            error="Please choose a reason so we know why you're leaving.",
        )

    business = business_by_id(business_id)
    if not business or not check_password_hash(business[3], password):
        return render_template(
            "delete_account_step3.html", reason_category=reason_category, reason_details=reason_details,
            error="Wrong password. Please try again.",
        ), 400

    record_account_deletion(business_id, reason_category, reason_details, source="web")
    delete_business(business_id)
    session.clear()
    return redirect(url_for("login", message="Your account has been permanently deleted. We're sorry to see you go."))


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


# robots.txt/sitemap.xml - helps search engines find and re-crawl the public marketing
# pages (esp. after content changes like the Gmail->Instagram DM pivot) instead of
# relying only on their own crawl schedule against stale cached copies.
@app.route("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {url_for('sitemap_xml', _external=True)}",
    ])
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = ["home", "login", "register", "terms", "privacy", "download"]
    urls = "".join(f"<url><loc>{url_for(page, _external=True)}</loc></url>" for page in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, mimetype="application/xml")


# Google Search Console domain-ownership verification (HTML file method) - the exact
# filename and body content are dictated by Search Console, not chosen by us; must
# keep serving this at the root even after verification succeeds, per Google's own
# instructions, so it's a permanent route rather than a one-off file to delete later.
@app.route("/googlea7b8cc332250151e.html")
def google_site_verification():
    return Response("google-site-verification: googlea7b8cc332250151e.html", mimetype="text/html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", logged_in=bool(session.get("business_id")))


# Public info page for Google Play's Data Safety "account deletion" link requirement -
# separate from POST /delete-account below (which performs the actual deletion and
# requires login). Reachable without logging in so it satisfies Play Console even for
# reviewers/users who can't sign in.
@app.route("/delete-account", methods=["GET"])
def delete_account_info():
    return render_template("delete_account_info.html", logged_in=bool(session.get("business_id")))


@app.route("/terms")
def terms():
    return render_template("terms.html", logged_in=bool(session.get("business_id")))


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
        # indices: 0 id, 1 name, 2 username, 4 contact_email, 6 instagram_username,
        # 11 phone_number, 12 email_verified, 13 phone_verified, 14 pending_contact_email,
        # 16 whatsapp_display_number, 19 messenger_page_name. subscription_status and
        # created_at are the two extras all_businesses_unfiltered() appends AFTER the
        # business columns, so they're read from the END (-2, -1) rather than by absolute
        # index: every time _BUSINESS_COLUMNS grows, an absolute index here silently
        # points at the wrong column, which is exactly how this route broke once before.
        instagram_username = (business[6] or "").lower()
        if search and search not in business[1].lower() and search not in business[2].lower() and search not in business[4].lower() and search not in instagram_username:
            continue
        result.append({
            "id": business[0],
            "name": business[1],
            "username": business[2],
            "contact_email": business[4],
            "instagram_username": business[6],
            "whatsapp_display_number": business[16],
            "messenger_page_name": business[19],
            "phone_number": business[11],
            "email_verified": business[12],
            "phone_verified": business[13],
            "subscription_status": business[-2],
            "created_at": business[-1].isoformat() if business[-1] else None,
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


# Founder-only view of /request-tester-access submissions - same secret-token gating as
# every other /admin route. Each row still needs a manual step in the Meta App dashboard
# (App roles > Roles > Instagram Testers) - nothing here does that automatically, there's
# no API for it.
@app.route("/admin/tester-requests")
def admin_tester_requests():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    requests_list = recent_tester_requests(limit=200)
    return render_template("admin_tester_requests.html", requests=requests_list)


# Founder-only view of the delete-account wizard's exit survey (web + mobile) - same
# secret-token gating as every other /admin route. This is the one place that reason a
# business gave for leaving is still readable after delete_business() has removed their
# row (see database.record_account_deletion).
@app.route("/admin/account-deletions")
def admin_account_deletions():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    deletions = recent_account_deletions(limit=200)
    return render_template("admin_account_deletions.html", deletions=deletions)


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
        auto_send=is_auto_send_enabled(session["business_id"]),
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


@app.route("/avatar/<int:conversation_id>")
@login_required
def conversation_avatar(conversation_id):
    """Live re-resolve of the sender's Instagram profile picture, used as the retry step
    in dashboard.html's avatar <img onerror> chain. The URL stored on the conversation at
    message-receipt time (see save_draft_reply/profile_pic_url) is a signed Meta CDN link
    that expires after a while, so by the time the dashboard is actually opened it can
    easily 403 even though the sender still has a picture - re-fetching it here on demand
    and redirecting to the fresh link keeps the real avatar showing instead of permanently
    regressing to the letter fallback."""
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    sender_igsid = conversation[6]
    if not sender_igsid:
        abort(404)
    business = business_by_id(session["business_id"])
    profile_pic_url = get_sender_info(sender_igsid, business[7])["profile_pic_url"]
    if not profile_pic_url:
        abort(404)
    return redirect(profile_pic_url)


def send_channel_reply(conversation, business, final_reply):
    """Sends final_reply through whichever channel `conversation` came in on - shared by
    the web send_reply route below and api.py's api_send_reply, so a manual send never
    needs its own per-channel branch anywhere else. conversation is a get_conversation()
    row: index 6 sender_igsid, 7 channel, 8 sender_wa_id, 9 sender_psid. Raises
    requests.RequestException on failure exactly like calling
    send_instagram_message/whatsapp_api.send_message/messenger_api.send_message directly
    would - callers already handle that (every channel fails the same way outside its 24h
    customer-reply window, which is an expected business outcome, not a bug)."""
    sender_igsid, channel, sender_wa_id, sender_psid = conversation[6], conversation[7], conversation[8], conversation[9]
    if channel == "whatsapp":
        return whatsapp_api.send_message(sender_wa_id, final_reply, business[17], business[15])
    if channel == "messenger":
        return messenger_api.send_message(sender_psid, final_reply, business[20])
    return send_instagram_message(sender_igsid, final_reply, business[7], business[5])


@app.route("/send/<int:conversation_id>", methods=["POST"])
@login_required
def send_reply(conversation_id):
    final_reply = request.form["reply"]

    conversation = get_conversation(conversation_id)
    _, business_id, sender, subject, question, suggested_reply, sender_igsid, channel, sender_wa_id, sender_psid = conversation

    # Security check: this conversation must belong to the logged-in business,
    # otherwise someone could guess other people's IDs in the URL and send replies on their behalf
    if business_id != session["business_id"]:
        abort(403)

    business = business_by_id(business_id)

    # Temporary diagnostic: the dashboard was showing "Sent" (the platform's API accepted
    # the request with 2xx) while the reply never reached the customer's real DM thread -
    # logging the platform's own response body here (viewable at /admin/client-errors) so
    # we can see exactly what it returned instead of guessing.
    debug_context = f"web-reply-send-debug conversation_id={conversation_id} channel={channel} sender_igsid={sender_igsid} sender_wa_id={sender_wa_id} sender_psid={sender_psid} final_reply={final_reply!r}"
    try:
        result = send_channel_reply(conversation, business, final_reply)
        log_client_error(business_id, f"{channel} send OK: {result}", "", debug_context, "")
    except requests.RequestException as e:
        error_body = e.response.text if getattr(e, "response", None) is not None else str(e)
        log_client_error(business_id, f"{channel} send FAILED: {error_body}", "", debug_context, "")
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
# Bounds for the "Custom" duration pill (both the business-wide default on the Schedule
# page header and the per-appointment override on the booking form) - wide enough for
# a quick 5-minute top-up or a half-day retreat, without allowing garbage input through.
SLOT_DURATION_MIN = 5
SLOT_DURATION_MAX = 480


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
    tz = booking.tz_for(business)
    slot_minutes = get_slot_duration(business_id)

    view = request.args.get("view", "day")
    if view not in ("day", "week"):
        view = "day"
    try:
        selected_date = date_type.fromisoformat(request.args.get("date", ""))
    except ValueError:
        selected_date = booking.now_local(tz).date()

    context = dict(
        locked=False,
        view=view,
        selected_date=selected_date,
        today=booking.now_local(tz).date(),
        slot_minutes=slot_minutes,
        slot_duration_choices=_duration_choices_with_custom(
            [(c, str(c)) for c in SLOT_DURATION_CHOICES], slot_minutes),
        slot_duration_min=SLOT_DURATION_MIN,
        slot_duration_max=SLOT_DURATION_MAX,
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
                "appointments": sorted(booking.appointments_for_local_day(business_id, d, tz), key=lambda a: a["start_local"]),
            })
        context["days"] = days
        context["prev_date"] = (week_start - timedelta(days=7)).isoformat()
        context["next_date"] = (week_start + timedelta(days=7)).isoformat()
    else:
        slots = booking.day_slots(working_hours, slot_minutes, selected_date, tz)
        day_appointments = booking.appointments_for_local_day(business_id, selected_date, tz)
        appts_by_start = {a["start_local"].strftime("%H:%M"): a for a in day_appointments}
        rows = []
        for s in slots:
            key = s.strftime("%H:%M")
            appt = appts_by_start.get(key)
            # An appointment booked with a custom duration (see create_appointment_route)
            # can run longer than one grid slot - the slots it swallows besides its own
            # start need to show as unavailable too, not a free "Book" button, even
            # though no appointment actually starts at their exact time.
            covered_by = None
            if appt is None:
                covered_by = next((a for a in day_appointments if a["start_local"] <= s < a["end_local"]), None)
            rows.append({
                "time": key,
                "end_time": (s + timedelta(minutes=slot_minutes)).strftime("%H:%M"),
                "appointment": appt,
                "covered_by": covered_by,
                "is_past": s < booking.now_local(tz),
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

    tz = booking.tz_for(business)
    try:
        local_dt = datetime.combine(date_type.fromisoformat(date_str), datetime.strptime(time_str, "%H:%M").time(), tzinfo=tz)
    except ValueError:
        return redirect(url_for("schedule", date=date_str))

    if customer_name:
        # The duration field on the booking form defaults to the business-wide slot
        # length but lets the owner pick a different one for this single appointment
        # (e.g. a 90-minute color when everything else is booked in 30-minute slots).
        # "custom" (picked via the form's own Custom option) reads from duration_custom
        # instead. Any out-of-range/garbage value falls back to the business default
        # rather than erroring, same "best-effort" spirit as the rest of this route.
        duration_raw = (request.form.get("duration") or "").strip()
        if not duration_raw or duration_raw == "custom":
            duration_raw = (request.form.get("duration_custom") or "").strip()
        try:
            duration_minutes = int(duration_raw)
        except ValueError:
            duration_minutes = get_slot_duration(business_id)
        if not (SLOT_DURATION_MIN <= duration_minutes <= SLOT_DURATION_MAX):
            duration_minutes = get_slot_duration(business_id)
        end_dt = local_dt + timedelta(minutes=duration_minutes)
        is_bookable = (
            local_dt >= booking.now_local(tz)
            and booking.slot_within_business_hours(business[8], local_dt, end_dt, tz)
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
    # Anything in the quick-pick list is always allowed; anything else (the "Custom"
    # pill on the Schedule page) just needs to be a sane number of minutes.
    if minutes not in SLOT_DURATION_CHOICES and not (SLOT_DURATION_MIN <= minutes <= SLOT_DURATION_MAX):
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


# --- Subscription (back on Paddle - see paddle_billing.py's module docstring for why.
# Stripe stays fully wired below (stripe_checkout/stripe_manage_billing/stripe_webhook)
# but unlinked from the pricing UI, same "kept live in the backend, not in the UI"
# treatment Paddle itself got when this briefly went the other way.) ---

def _subscription_response(business_id, hide_nav=False, trial_ended=False):
    business = business_by_id(business_id)
    status = get_subscription_status(business_id)
    active = effective_subscription_active(business_id)
    return render_template(
        "subscription.html",
        paddle_configured=paddle_billing.is_configured(),
        paddle_client_token=paddle_billing.client_token(),
        paddle_environment=paddle_billing.environment(),
        # Starter/Pro x Monthly/Yearly - four distinct Paddle prices, each its own env
        # var. A price whose env var isn't set on Railway yet just shows "Coming soon"
        # on that button - see paddle_billing.price_id_for.
        paddle_prices={
            "starter_monthly": paddle_billing.price_id_for("starter", "monthly"),
            "starter_yearly": paddle_billing.price_id_for("starter", "yearly"),
            "pro_monthly": paddle_billing.price_id_for("pro", "monthly"),
            "pro_yearly": paddle_billing.price_id_for("pro", "yearly"),
        },
        business_id=business_id,
        email=business[4],
        status=status,
        active=active,
        pro_access=BETA_FREE_MODE or has_pro_access(business_id),
        has_paddle_billing=bool(get_paddle_customer_id(business_id)),
        hide_nav=hide_nav,
        # A freshly created account (never started a trial/subscription, and not a comp
        # account either - see database.is_comp_account) can't use Home/Conversations/
        # Schedule/Profile/Settings yet anyway - subscription_required just bounces it
        # straight back here - so the top bar is trimmed down to the brand and the
        # language picker (in base.html) until a plan is chosen.
        minimal_nav=(status == "none" and not active),
        trial_ended=trial_ended,
    )


@app.route("/subscription")
@login_required
def subscription():
    return _subscription_response(session["business_id"], trial_ended=bool(request.args.get("trial_ended")))


# The "Free" button next to every Pro-locked feature (Schedule, no-show reminders, AI
# upsell) - see grant_free_pro_access()'s docstring in database.py for why this is a
# straight plan flip instead of a checkout flow. `next` is a whitelisted endpoint name,
# not a raw URL, so this can't be turned into an open redirect.
UPGRADE_FREE_REDIRECT_ENDPOINTS = ("schedule", "app_settings", "settings", "subscription")


@app.route("/subscription/upgrade-free", methods=["POST"])
@login_required
def upgrade_to_pro_free():
    grant_free_pro_access(session["business_id"])
    next_endpoint = request.form.get("next")
    if next_endpoint not in UPGRADE_FREE_REDIRECT_ENDPOINTS:
        next_endpoint = "home"
    return redirect(url_for(next_endpoint, upgraded=1))


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

    # A business gets a real, card-backed 14-day Stripe trial the first time it ever
    # checks out (no stripe_customer_id on file yet) - same "card required, nothing
    # charged until the trial ends" pattern as most subscription apps, see
    # stripe_billing.create_checkout_session. Anyone checking out again after that (a
    # past cancellation, a plan change) goes straight to a normal paid subscription -
    # otherwise a canceled account could just re-checkout for another free trial.
    is_first_checkout = not get_stripe_customer_id(business_id)
    checkout_url = stripe_billing.create_checkout_session(
        business_id, business[4], plan, interval,
        success_url=url_for("home", _external=True),
        cancel_url=url_for("subscription", _external=True),
        trial_period_days=14 if is_first_checkout else None,
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
            # The Checkout Session payload never carries the new subscription's own
            # status - a trial_period_days checkout (see stripe_checkout()) starts
            # 'trialing', not 'active', and effective_subscription_active()/
            # has_pro_access() both key off exactly that distinction.
            status = stripe_billing.get_subscription_status(subscription_id)
            link_stripe_subscription(int(business_id), customer_id, subscription_id, status, plan=plan)

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

    elif event_type == "customer.subscription.trial_will_end":
        # Stripe fires this 3 days before a trialing subscription's first real charge
        # (by default - configurable in the Stripe Dashboard) - our cue to send our own
        # branded heads-up email before their card is charged, same "warn a few days
        # before renewal" pattern most subscription apps follow instead of just
        # silently charging the card, see subscription.active.trialBody's copy too.
        subscription_id = data.get("id")
        business = business_by_stripe_subscription_id(subscription_id) if subscription_id else None
        if business:
            trial_end = data.get("trial_end")
            trial_end_text = (
                datetime.fromtimestamp(trial_end, tz=timezone.utc).strftime("%B %d")
                if trial_end else "in a few days"
            )
            send_email(
                recipient=business[4],
                subject="Your Camilius trial is ending soon",
                body=(
                    f"Hi {business[1]},\n\n"
                    f"Your 14-day free trial ends on {trial_end_text}. After that, we'll "
                    f"charge the card on file to keep the AI processing your Instagram "
                    f"messages without interruption.\n\n"
                    f"Nothing to do if you want to continue - it's automatic. If you'd "
                    f"rather not continue, you can cancel any time before then from "
                    f"Subscription > Manage billing in your dashboard, and you won't be "
                    f"charged anything.\n\n"
                    f"- Camilius"
                ),
                business_name="Camilius",
                reply_to=os.getenv("ALERT_EMAIL") or business[4],
            )

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
        # While trialing, next_billed_at IS the trial's end date (the moment Paddle will
        # actually charge the card) - reminders.py's send_trial_ending_warnings_if_needed
        # polls on this to know when to send our own "trial ending soon" email, since
        # Paddle Billing has no webhook event for that the way Stripe does.
        trial_ends_at = None
        next_billed_at = data.get("next_billed_at")
        if next_billed_at:
            try:
                trial_ends_at = datetime.fromisoformat(next_billed_at.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                pass

        if business_id:
            link_subscription(int(business_id), customer_id, sub_id, status, plan=plan, trial_ends_at=trial_ends_at)
        elif sub_id:
            update_subscription_status(sub_id, status, plan=plan, trial_ends_at=trial_ends_at)

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
    # still gets processed through its own 14-day self-serve trial (subscription_status=
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
    sender_info = get_sender_info(sender_igsid, access_token)
    sender_display = sender_info["display_name"] or sender_igsid
    profile_pic_url = sender_info["profile_pic_url"]

    print(f"[instagram-webhook] Generating AI reply for text={message['text']!r} from sender_igsid={sender_igsid}", flush=True)
    # sender_display, NOT sender_igsid: previous_conversations_with filters on the
    # conversations.sender column, and save_draft_reply (below) writes sender_display
    # into it. Passing the IGSID here matched nothing whenever get_sender_info actually
    # resolved a username, so Instagram conversations silently had no memory at all -
    # the bug hid because sender_display falls back to sender_igsid when the profile
    # lookup fails, which is exactly the case that did work. The WhatsApp and Messenger
    # processors below already pass sender_display.
    history = previous_conversations_with(business_id, sender_display)

    # Faza 8/9: Pro-plan (or trialing) businesses get a smarter AI that can check the
    # Schedule for a free slot and book, cancel, or reschedule an appointment directly
    # during the DM conversation - see generate_reply.generate_reply_with_booking/booking.py.
    if BETA_FREE_MODE or has_pro_access(business_id):
        slot_minutes = get_slot_duration(business_id)
        upsell_pairs_text = get_upsell_pairs(business_id)
        # The business's own clock, not the server's - this string is what the model
        # resolves "tomorrow" / "this Friday" against before calling a booking tool.
        now_local_display = booking.now_local(booking.tz_for(business)).strftime("%A, %Y-%m-%d %H:%M")

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
        profile_pic_url=profile_pic_url,
    )
    print(f"[instagram-webhook] Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    # Opt-out (default on, see database.py's businesses.auto_send migration) - the AI's
    # draft goes straight out unless a business turns this off in Settings, in which
    # case it waits for manual approval instead. Falls back to a normal pending draft
    # if the send itself fails (most likely Instagram's 24h reply window), same as the
    # dashboard's own manual send route.
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


@app.route("/whatsapp-webhook", methods=["GET", "POST"])
@csrf.exempt  # external request from Meta, no session/CSRF token of ours - signed instead (checked below)
def whatsapp_webhook():
    """Mirrors instagram_webhook() above - same verify/signature/dedup/retry shape, just
    against whatsapp_api's functions and business_by_whatsapp_phone_number_id instead of
    the Instagram equivalents. Kept as its own separate route+handler rather than a shared
    parameterized one - the two channels' underlying data (profile-pic lookup, sender-name
    resolution) differ enough that a shared abstraction would need almost as many
    channel-specific branches as just writing it twice plainly does."""
    if request.method == "GET":
        try:
            log_webhook_hit("WA GET verify", str(dict(request.args)))
        except Exception:
            pass
        challenge = whatsapp_api.verify_webhook_challenge(
            request.args.get("hub.mode"),
            request.args.get("hub.verify_token"),
            request.args.get("hub.challenge"),
        )
        if challenge is None:
            abort(403)
        return challenge, 200

    raw_body = request.get_data()

    sig_ok = whatsapp_api.verify_webhook_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""))
    try:
        log_webhook_hit(f"WA POST sig_ok={sig_ok}", raw_body.decode("utf-8", errors="replace"))
    except Exception:
        pass

    if not sig_ok:
        abort(403)

    payload = request.get_json(silent=True) or {}
    messages = whatsapp_api.parse_webhook_messages(payload)
    for message in messages:
        try:
            _process_whatsapp_message(message)
        except Exception as error:
            print(f"[whatsapp-webhook] Error processing message: {error!r}", flush=True)

    return "", 200


def _process_whatsapp_message(message):
    business = business_by_whatsapp_phone_number_id(message["phone_number_id"])
    if business is None:
        print(f"[whatsapp-webhook] No business found for phone_number_id={message['phone_number_id']!r} - ignoring", flush=True)
        return

    business_id = business[0]
    print(f"[whatsapp-webhook] Matched business_id={business_id} name={business[1]!r}", flush=True)

    if not BETA_FREE_MODE and not effective_subscription_active(business_id):
        print(f"[whatsapp-webhook] business_id={business_id} subscription not active - skipping", flush=True)
        return
    if replies_today_count(business_id) >= DAILY_REPLY_LIMIT:
        print(f"[whatsapp-webhook] business_id={business_id} daily limit reached - skipping", flush=True)
        return

    message_id = message["message_id"]
    if message_id and already_processed_messages(business_id, [message_id]):
        print(f"[whatsapp-webhook] message_id={message_id} already processed - skipping (dedup)", flush=True)
        return

    sender_wa_id = message["sender_wa_id"]
    access_token = business[17]
    # No profile-picture equivalent to Instagram's get_sender_info here - WhatsApp's Cloud
    # API doesn't expose one - so profile_pic_url stays None and the dashboard/app fall
    # back to the letter avatar for every WhatsApp conversation, same as any Instagram
    # sender whose picture lookup fails. The display name, unlike Instagram, comes free
    # with every message webhook payload (message["sender_name"]) - no extra API call
    # needed for that part.
    sender_display = message["sender_name"] or sender_wa_id
    business_info = {
        "business_name": business[1],
        "working_hours": business[8],
        "price_list": business[9],
        "faq": business[10],
    }

    print(f"[whatsapp-webhook] Generating AI reply for text={message['text']!r} from sender_wa_id={sender_wa_id}", flush=True)
    history = previous_conversations_with(business_id, sender_display)

    if BETA_FREE_MODE or has_pro_access(business_id):
        slot_minutes = get_slot_duration(business_id)
        upsell_pairs_text = get_upsell_pairs(business_id)
        # The business's own clock, not the server's - this string is what the model
        # resolves "tomorrow" / "this Friday" against before calling a booking tool.
        now_local_display = booking.now_local(booking.tz_for(business)).strftime("%A, %Y-%m-%d %H:%M")

        def book_tool(requested_datetime, service=None):
            return booking.attempt_ai_booking(
                business, business_id, slot_minutes, requested_datetime, sender_display, sender_wa_id, service, upsell_pairs_text,
            )

        def cancel_tool():
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_wa_id, None, upsell_pairs_text,
            )

        def reschedule_tool(new_requested_datetime):
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_wa_id, new_requested_datetime, upsell_pairs_text,
            )

        tool_impls = {
            "check_and_book_appointment": book_tool,
            "cancel_appointment": cancel_tool,
            "reschedule_appointment": reschedule_tool,
        }
        suggested_reply = generate_reply_with_booking(message["text"], business_info, history, now_local_display, tool_impls)
    else:
        suggested_reply = generate_reply(message["text"], business_info, history=history)
    print(f"[whatsapp-webhook] AI reply generated: {suggested_reply!r}", flush=True)

    new_conversation_id = save_draft_reply(
        business_id, sender_display, message["text"], suggested_reply,
        channel="whatsapp", sender_wa_id=sender_wa_id,
    )
    print(f"[whatsapp-webhook] Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    auto_sent = False
    if is_auto_send_enabled(business_id):
        try:
            whatsapp_api.send_message(sender_wa_id, suggested_reply, access_token, business[15])
            mark_sent(new_conversation_id, suggested_reply)
            auto_sent = True
            print(f"[whatsapp-webhook] Auto-sent reply for conversation_id={new_conversation_id}", flush=True)
        except requests.RequestException as error:
            print(f"[whatsapp-webhook] Auto-send failed for conversation_id={new_conversation_id}, left as pending: {error}", flush=True)

    try:
        send_push_to_business(
            business_id,
            title="Auto-replied to a customer" if auto_sent else "New customer question",
            body=f"{sender_display}: {message['text'][:80]}",
            data={"conversation_id": str(new_conversation_id)},
        )
    except Exception as error:
        print(f"[whatsapp-webhook] Push notification failed: {error}", flush=True)


@app.route("/messenger-webhook", methods=["GET", "POST"])
@csrf.exempt  # external request from Meta, no session/CSRF token of ours - signed instead (checked below)
def messenger_webhook():
    """Mirrors whatsapp_webhook() above - same verify/signature/dedup/retry shape,
    against messenger_api's functions and business_by_messenger_page_id instead."""
    if request.method == "GET":
        try:
            log_webhook_hit("FB GET verify", str(dict(request.args)))
        except Exception:
            pass
        challenge = messenger_api.verify_webhook_challenge(
            request.args.get("hub.mode"),
            request.args.get("hub.verify_token"),
            request.args.get("hub.challenge"),
        )
        if challenge is None:
            abort(403)
        return challenge, 200

    raw_body = request.get_data()

    sig_ok = messenger_api.verify_webhook_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""))
    try:
        log_webhook_hit(f"FB POST sig_ok={sig_ok}", raw_body.decode("utf-8", errors="replace"))
    except Exception:
        pass

    if not sig_ok:
        abort(403)

    payload = request.get_json(silent=True) or {}
    messages = messenger_api.parse_webhook_messages(payload)
    for message in messages:
        try:
            _process_messenger_message(message)
        except Exception as error:
            print(f"[messenger-webhook] Error processing message: {error!r}", flush=True)

    return "", 200


def _process_messenger_message(message):
    business = business_by_messenger_page_id(message["page_id"])
    if business is None:
        print(f"[messenger-webhook] No business found for page_id={message['page_id']!r} - ignoring", flush=True)
        return

    business_id = business[0]
    print(f"[messenger-webhook] Matched business_id={business_id} name={business[1]!r}", flush=True)

    if not BETA_FREE_MODE and not effective_subscription_active(business_id):
        print(f"[messenger-webhook] business_id={business_id} subscription not active - skipping", flush=True)
        return
    if replies_today_count(business_id) >= DAILY_REPLY_LIMIT:
        print(f"[messenger-webhook] business_id={business_id} daily limit reached - skipping", flush=True)
        return

    message_id = message["message_id"]
    if message_id and already_processed_messages(business_id, [message_id]):
        print(f"[messenger-webhook] message_id={message_id} already processed - skipping (dedup)", flush=True)
        return

    sender_psid = message["sender_psid"]
    access_token = business[20]
    # No sender-name/profile-pic resolution here, unlike Instagram's get_sender_info - the
    # Messenger user-profile API has been heavily permission-gated since Meta's 2018
    # privacy changes and isn't reliably available with just pages_messaging, so this
    # falls back to the raw PSID as the display name, same as WhatsApp does when its own
    # profile lookup has nothing to offer.
    sender_display = sender_psid
    business_info = {
        "business_name": business[1],
        "working_hours": business[8],
        "price_list": business[9],
        "faq": business[10],
    }

    print(f"[messenger-webhook] Generating AI reply for text={message['text']!r} from sender_psid={sender_psid}", flush=True)
    history = previous_conversations_with(business_id, sender_display)

    if BETA_FREE_MODE or has_pro_access(business_id):
        slot_minutes = get_slot_duration(business_id)
        upsell_pairs_text = get_upsell_pairs(business_id)
        # The business's own clock, not the server's - this string is what the model
        # resolves "tomorrow" / "this Friday" against before calling a booking tool.
        now_local_display = booking.now_local(booking.tz_for(business)).strftime("%A, %Y-%m-%d %H:%M")

        def book_tool(requested_datetime, service=None):
            return booking.attempt_ai_booking(
                business, business_id, slot_minutes, requested_datetime, sender_display, sender_psid, service, upsell_pairs_text,
            )

        def cancel_tool():
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_psid, None, upsell_pairs_text,
            )

        def reschedule_tool(new_requested_datetime):
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_psid, new_requested_datetime, upsell_pairs_text,
            )

        tool_impls = {
            "check_and_book_appointment": book_tool,
            "cancel_appointment": cancel_tool,
            "reschedule_appointment": reschedule_tool,
        }
        suggested_reply = generate_reply_with_booking(message["text"], business_info, history, now_local_display, tool_impls)
    else:
        suggested_reply = generate_reply(message["text"], business_info, history=history)
    print(f"[messenger-webhook] AI reply generated: {suggested_reply!r}", flush=True)

    new_conversation_id = save_draft_reply(
        business_id, sender_display, message["text"], suggested_reply,
        channel="messenger", sender_psid=sender_psid,
    )
    print(f"[messenger-webhook] Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    auto_sent = False
    if is_auto_send_enabled(business_id):
        try:
            messenger_api.send_message(sender_psid, suggested_reply, access_token)
            mark_sent(new_conversation_id, suggested_reply)
            auto_sent = True
            print(f"[messenger-webhook] Auto-sent reply for conversation_id={new_conversation_id}", flush=True)
        except requests.RequestException as error:
            print(f"[messenger-webhook] Auto-send failed for conversation_id={new_conversation_id}, left as pending: {error}", flush=True)

    try:
        send_push_to_business(
            business_id,
            title="Auto-replied to a customer" if auto_sent else "New customer question",
            body=f"{sender_display}: {message['text'][:80]}",
            data={"conversation_id": str(new_conversation_id)},
        )
    except Exception as error:
        print(f"[messenger-webhook] Push notification failed: {error}", flush=True)


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
