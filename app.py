import os
import re
import hmac
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone, date as date_type
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory, Response, g
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
    raw_conversation_rows,
    business_by_username, business_by_phone_number,
    username_taken, contact_email_taken, phone_number_taken,
    business_by_google_id, link_google_account,
    create_oauth_login_session, complete_oauth_login_session, fail_oauth_login_session,
    complete_oauth_login_session_needs_signup, oauth_login_session_exists, oauth_login_session_intent,
    fail_oauth_login_session_no_account,
    archived_conversations_for_business, trashed_conversations_for_business,
    archive_conversation, unarchive_conversation, soft_delete_conversation, restore_conversation,
    get_slot_duration, set_slot_duration, create_appointment, get_appointment, cancel_appointment,
    appointments_in_range,
    get_upsell_pairs, set_upsell_pairs,
    is_owner_reminders_enabled, set_owner_reminders_enabled, get_owner_reminder_minutes, set_owner_reminder_minutes,
    all_appointments_for_business, get_stripe_customer_id, get_paddle_customer_id,
    save_email_verify_token, business_by_email_verify_token, mark_email_verified, email_verify_sent_recently,
    email_verify_resend_wait_seconds, save_pending_email_change, confirm_pending_email_change,
    save_email_verify_code, business_email_verify_code_matches,
    save_phone_verify_token, business_by_phone_verify_token, mark_phone_verified, phone_verify_sent_recently,
    phone_verify_resend_wait_seconds, save_phone_verify_code,
    grant_free_pro_access,
    notifications_for_business, unread_notification_count, mark_notifications_read,
    is_new_booking_notifications_enabled, set_new_booking_notifications_enabled,
    record_account_deletion, recent_account_deletions, ACCOUNT_DELETION_REASONS,
    get_business_page, page_by_slug, upsert_business_page, published_page_slugs,
    page_chat_session_messages_today,
    onboarding_completed, mark_onboarding_completed,
    UniqueViolation, release_db_lock,
)
from send_email import send_email
from send_sms import send_sms, sms_configured
from support_assistant import answer_support_question
import ai_replies
import booking
import pages
import themes
from page_generator import (
    generate_page_draft, format_working_hours, format_price_list, format_faq,
    transcribe_voice_description, MAX_VOICE_AUDIO_BYTES,
)
import stripe_billing
import paddle_billing
import google_oauth
import business_media
import business_documents
import ai_theme
from database import all_businesses_unfiltered
from api import api as api_blueprint, APK_VERSION, make_token
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

# F5 (2026-08-05): flipped back on - the business behind this app isn't legally
# registered yet, so it can't take real payments for about another month. All
# billing/pricing/plan code (Stripe, Paddle, the Starter/Pro tiers) stays exactly as
# it was - this flag just goes back to hiding it from users and granting everyone full
# access, same mechanism as the original beta period and the same "flag it, don't
# delete it" shape as INSTAGRAM_TESTERS_ONLY/META_CHANNELS_ENABLED. Flip back to False
# once the business is registered and ready to actually charge.
BETA_FREE_MODE = True


@app.context_processor
def inject_beta_free_mode():
    return {"beta_free_mode": BETA_FREE_MODE}


# Voluntary "support the project" donation link (PayPal.me or similar), shown on the
# subscription page and site footer instead of real billing while BETA_FREE_MODE is on.
# Unset by default so the button/card stay hidden until DONATION_LINK is configured on
# Railway - never renders a dead link.
@app.context_processor
def inject_donation_link():
    return {"donation_link": os.getenv("DONATION_LINK", "").strip() or None}


# Gates the "Continue with Google" button on login.html/register.html - unset
# GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET (see google_oauth.is_configured()) hides it
# entirely rather than showing a button that would 404, same "ship inert until
# configured" pattern as donation_link/BETA_FREE_MODE above and Paddle/Stripe elsewhere.
@app.context_processor
def inject_google_oauth_configured():
    return {"google_oauth_configured": google_oauth.is_configured()}


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
    "hours-picker.js": _static_file_version("hours-picker.js"),
    "pickers.js": _static_file_version("pickers.js"),
}
_FOREVER_CACHE_STATIC_PATHS = {"/static/style.css", "/static/htmx.min.js", "/static/site-i18n.js", "/static/hours-picker.js", "/static/pickers.js"}
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
# frame-src also needs 'self' - the onboarding design step (onboarding_design.html)
# embeds this site's own /onboarding/website/preview in an <iframe> for each theme card's
# live thumbnail. Without it every one of those 5 previews silently renders as
# chrome-error://chromewebdata (CSP blocks the frame navigation itself, not the route -
# a plain fetch() of the same URL succeeds fine, since that's governed by connect-src,
# not frame-src) - caught visually in a real browser, not by the route/template tests.
# {frame_ancestors} is filled in by set_security_headers below - 'self' for every normal
# response, but the one exception (GET /w/<slug>, the F5 embeddable widget - see
# widget_frame()) needs a per-business external origin instead, since that response is
# supposed to be framed by someone else's page.
_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.paddle.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    # ipapi.co: best-effort IP geolocation the language picker uses to guess a first-time
    # visitor's language from their country (static/site-i18n.js's detectLanguageFromLocation)
    # - without this allowance every one of those requests was silently CSP-blocked, so the
    # feature never actually worked (caught testing this, not by design).
    "connect-src 'self' https://*.paddle.com https://ipapi.co; "
    "frame-src 'self' https://*.paddle.com; "
    "frame-ancestors {frame_ancestors}; "
    "base-uri 'self'; "
    "object-src 'none'"
)
_CSP = _CSP_TEMPLATE.format(frame_ancestors="'self'")


@app.after_request
def set_security_headers(response):
    if request.path.startswith("/w/"):
        # The embeddable widget iframe (widget_frame() above) - the one response on this
        # whole site that's SUPPOSED to be framed by someone else's page. It stashes the
        # business's own approved embed origin on flask.g while rendering (no extra DB
        # lookup here); nothing set (no domain approved yet, or the route 404'd before
        # reaching that line) falls back to 'none' - deny framing entirely, the same safe
        # default a business that's never touched the widget-domain field is already in.
        # X-Frame-Options has no syntax for "this one specific external origin" (only
        # SAMEORIGIN/DENY/the dead ALLOW-FROM), so it's omitted entirely here rather than
        # set to something that would just contradict the CSP frame-ancestors below,
        # which is what every modern browser actually honors for this.
        ancestor = getattr(g, "widget_frame_ancestor", None)
        response.headers["Content-Security-Policy"] = _CSP_TEMPLATE.format(
            frame_ancestors=f"'self' {ancestor}" if ancestor else "'none'"
        )
    else:
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not app.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Releases database.connect()'s per-request lock (see its own docstring) unconditionally
# at the end of every request - teardown_request runs even when the view raised, so one
# route that errors before its own connection.close() can't leave the whole app's DB
# access wedged for every request after it.
@app.teardown_request
def _release_database_lock(exception=None):
    release_db_lock()


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


# Faza 13: sends a logged-in business straight into the mandatory "build your website"
# wizard (onboarding_website_describe and the steps after it) until it's done - see
# database.onboarding_completed/mark_onboarding_completed. Stacked on home() only, the
# same single-choke-point reasoning as verify_email()/login() both already redirecting
# there on success: every path into the logged-in app passes through home() first, so
# gating it here is enough without touching every other @login_required route. A
# business that existed before this wizard shipped is grandfathered in (see
# create_database's onboarding_backfill migration) and never hits this redirect.
def onboarding_required(function):
    @wraps(function)
    def wrapped_function(*args, **kwargs):
        business_id = session.get("business_id")
        if business_id and not onboarding_completed(business_id):
            return redirect(url_for("onboarding_website_describe"))
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


def _google_login_existing(google_user):
    """Looks up (and links, on the first match) the existing business this Google identity
    belongs to - never creates one. Returns the business row, or None if this Google
    account has never been seen here before. The caller (auth_google_callback below) sends
    that case to the ordinary sign-up form instead, pre-filled with whatever Google handed
    over - "Continue with Google" is a shortcut INTO the normal flow for a first-time
    visitor, not a way to skip filling in the rest of the account (username, password,
    business details) that a manual sign-up would ask for anyway.

    Two cases, in order:
    1. This Google account has signed in here before (google_id already linked) - just
       log it in.
    2. First time signing in with Google, but its email already belongs to an existing
       account (password-based, or from an earlier signup that never got confirmed) -
       link the two rather than sending it through sign-up again. link_google_account
       also marks that account's email verified, since Google just proved it either way.
    """
    google_id = google_user.get("sub")
    email = (google_user.get("email") or "").strip().lower()
    if not google_id:
        return None

    business = business_by_google_id(google_id)
    if business:
        return business

    if email:
        existing = business_by_contact_email(email)
        if existing:
            link_google_account(existing[0], google_id)
            return business_by_id(existing[0])

    return None


@app.route("/auth/google")
@limiter.limit("20 per 15 minutes")
def auth_google():
    """Entry point for the web "Continue with Google" button (login.html/register.html) -
    the mobile app never hits this route at all, it builds the same authorization_url
    itself via /api/google-login/start (see api.py) since it has no session cookie to
    round-trip through. CSRF is checked here the ordinary web way: `state` is stashed in
    Flask's own session cookie and compared back in auth_google_callback below.

    `?intent=login` (set by login.html's button only) is stashed alongside it - it's how
    auth_google_callback tells "Continue with Google" clicked on the Login screen apart
    from Create Account, so a brand-new identity from Login gets an error instead of being
    handed off to the sign-up form."""
    if not google_oauth.is_configured():
        abort(404)
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["oauth_intent"] = "login" if request.args.get("intent") == "login" else "register"
    redirect_uri = url_for("auth_google_callback", _external=True)
    return redirect(google_oauth.authorization_url(redirect_uri, state))


@app.route("/auth/google/callback")
def auth_google_callback():
    """Google redirects here after the consent screen, for both the web flow (started by
    auth_google() above) and the mobile flow (started by api.py's
    api_google_login_start()) - a single registered redirect URI keeps the Google Cloud
    OAuth client config to one entry instead of two. Which flow this is is told apart by
    oauth_login_session_exists(state): only api_google_login_start() ever creates one of
    those rows, so its presence IS the "this is mobile" signal - no query param needed,
    and nothing a visitor could spoof to switch flows since `state` is an unguessable
    token either way."""
    if not google_oauth.is_configured():
        abort(404)

    state = request.args.get("state", "")
    is_mobile = oauth_login_session_exists(state)

    def fail(message):
        if is_mobile:
            fail_oauth_login_session(state)
            return redirect(url_for("auth_google_done", state=state, outcome="error"))
        return redirect(url_for("login", message=message))

    # Which screen "Continue with Google" was clicked on - Login or Create Account (see
    # auth_google()'s and api_google_login_start()'s own comments on where this came from).
    # Only matters below, once we know whether an existing account was actually found.
    if is_mobile:
        intent = oauth_login_session_intent(state)
    else:
        intent = session.pop("oauth_intent", "register")

    # Web-only CSRF check - the mobile state token is its own proof (created server-side by
    # api_google_login_start(), unguessable), no session cookie involved for that flow at all.
    if not is_mobile:
        expected_state = session.pop("oauth_state", None)
        if not expected_state or expected_state != state:
            return fail("Google sign-in failed - please try again.")

    if request.args.get("error") or "code" not in request.args:
        return fail("Google sign-in was cancelled.")

    redirect_uri = url_for("auth_google_callback", _external=True)
    google_user = google_oauth.fetch_user(request.args["code"], redirect_uri)
    if google_user is None:
        return fail("Google sign-in failed - please try again.")
    google_id = google_user.get("sub")
    if not google_id:
        return fail("Google sign-in failed - please try again.")

    business = _google_login_existing(google_user)

    if business is None:
        # Never seen this Google identity before. Clicked from the Login screen
        # (intent == "login"): the visitor asked to log in, not sign up, so this must
        # behave like a failed login - no account created, no sign-up form - not silently
        # start a new account for them.
        if intent == "login":
            if is_mobile:
                fail_oauth_login_session_no_account(state)
                return redirect(url_for("auth_google_done", state=state, outcome="no_account"))
            return redirect(url_for("login", error="No Camilius account is linked to that Google account. Please register first."))

        # Otherwise (Create Account screen) - hand its name/email to the sign-up form as a
        # starting point rather than creating an account outright (see
        # _google_login_existing's own docstring). google_link_token carries google_id
        # through so register()/api_register() can link it - and skip the usual email
        # confirmation step, since Google already proved this address - IF the sign-up is
        # actually completed with that same email; see google_oauth.verify_link_token.
        name = google_user.get("name") or ""
        email = (google_user.get("email") or "").strip().lower()
        link_token = google_oauth.make_link_token(google_id, email)
        if is_mobile:
            complete_oauth_login_session_needs_signup(state, name, email, link_token)
            return redirect(url_for("auth_google_done", state=state, outcome="needs_signup"))
        return redirect(url_for("register", google_name=name, google_email=email, google_link_token=link_token))

    if is_mobile:
        token = make_token(business[0])
        complete_oauth_login_session(state, token, business[0], business[1])
        return redirect(url_for("auth_google_done", state=state))

    session["business_id"] = business[0]
    session["business_name"] = business[1]
    return redirect(url_for("home"))


@app.route("/auth/google/done")
def auth_google_done():
    """Verified Android App Link target for the "Continue with Google" mobile bridge -
    see AndroidManifest.xml's intent-filter for this exact path (autoVerify, host
    app.ai-auto-reply-mail.com - matches mobile/app.js's own API_BASE) and
    mobile/app.js's appUrlOpen listener. auth_google_callback
    above redirects here for the mobile flow ONLY - and only once it's already fully
    resolved the attempt (exchanged the code, resolved/linked the business, updated
    oauth_login_sessions). Never earlier: Google's own redirect must still land on
    auth_google_callback normally to hand over `code` in the first place - if this path
    were the one Google redirected to, Android would intercept that first hop before it
    ever reached the server to do the exchange at all.

    On a signed APK that has the manifest entry, Android hands this URL straight to the
    app before Chrome/the Custom Tab ever loads it - closing that tab as part of the OS
    handoff, with nothing rendered in it. This route's own response below is only ever
    seen as a fallback: an older installed APK without that manifest entry yet, or an
    Android/Chrome combination that doesn't honor the App Link mid-Custom-Tab. Either
    way, `outcome` (plain display info, not a credential - the actual result still has to
    come from /api/google-login/status, exactly as before this route existed) says which
    of auth_google_callback's own pages to fall back to."""
    if not google_oauth.is_configured():
        abort(404)
    outcome = request.args.get("outcome")
    if outcome == "no_account":
        return render_template("google_auth_mobile_result.html", no_account=True)
    if outcome == "needs_signup":
        return render_template("google_auth_mobile_result.html", needs_signup=True)
    if outcome == "error":
        return render_template("google_auth_mobile_result.html")
    # No outcome (or an unrecognized one) - the plain "it worked" case: a bare,
    # resource-free page (no base.html chrome - see its own docstring), nothing for the
    # visitor to read before the app's own poll (or this same App Link, next time) closes
    # this tab.
    return render_template("google_auth_mobile_blank.html")


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

    return render_template(
        "login.html", error=request.args.get("error"), message=request.args.get("message"),
        account_deleted=request.args.get("account_deleted") == "1",
    )


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

        # "Continue with Google" prefilled this form (see auth_google_callback) rather
        # than creating an account outright - the hidden google_link_token field carries
        # google_id back through, re-verified here (never trusted from the client alone)
        # and only honored if the email it was issued for still matches what was actually
        # submitted. A visitor who changed the email field after Google prefilled it just
        # falls back to a normal, unverified signup - exactly as if they'd never used
        # Google at all.
        google_link = google_oauth.verify_link_token(request.form.get("google_link_token", ""))
        google_id = None
        if google_link and google_link.get("email") == request.form["contact_email"].strip().lower():
            google_id = google_link.get("google_id")

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
                google_id=google_id,
                email_verified=bool(google_id),
            )
        except UniqueViolation:
            return render_template("register.html", error="That username, contact email, or phone number is already taken.", data=request.form)

        log_event("register_completed", business_id=new_id)

        if google_id:
            # Google already proved this email - no confirmation link to send or wait on,
            # straight into the app exactly like a normal login would.
            session["business_id"] = new_id
            session["business_name"] = request.form["name"]
            return redirect(url_for("home"))

        send_verification_email(new_id, request.form["name"], request.form["contact_email"])
        # Not logged in yet - the account must confirm its email first (see verify_pending()
        # /verify_email() below).
        session["pending_verify_business_id"] = new_id
        return redirect(url_for("verify_pending"))

    if not session.get("_seen_register"):
        log_event("register_view")
        session["_seen_register"] = True

    # Prefill data from a "Continue with Google" redirect (see auth_google_callback) - a
    # first-time Google identity with no account yet lands here instead of one being
    # created outright, with whatever Google handed over as a starting point for the
    # fields it can actually cover (name/email); the rest (username, password, business
    # details) is still entered by hand exactly like a normal sign-up. google_link_token
    # is echoed back as a hidden field (see register.html) purely to survive this one
    # render - it's re-verified server-side on submit, never trusted as-is.
    data = {}
    if request.args.get("google_link_token"):
        data = {
            "name": request.args.get("google_name", ""),
            "contact_email": request.args.get("google_email", ""),
            "google_link_token": request.args.get("google_link_token", ""),
        }
    return render_template("register.html", error=None, data=data)


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
    return render_template("verify_pending.html", contact_email=business[4], resent=False, resend_wait_seconds=wait_seconds, error=None)


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
    return render_template("verify_pending.html", contact_email=business[4], resent=True, resend_wait_seconds=wait_seconds, error=None)


# Redeems the code emailed by send_verification_email() - the typed-code counterpart to
# /verify-email/<token>'s clicked link (still used for the Settings email-change flow
# only). Session-based like the rest of verify_pending - pending_verify_business_id is
# set by register()/login() above and is what ties a submitted code back to the right
# account, so a wrong guess here can't be reused to probe other accounts' codes.
@app.route("/verify-pending/confirm", methods=["POST"])
@limiter.limit("10 per 15 minutes")
def verify_pending_confirm():
    business_id = session.get("pending_verify_business_id")
    if not business_id:
        return redirect(url_for("login"))
    business = business_by_id(business_id)
    if business is None or business[12]:  # already verified (or account gone) - nothing pending
        session.pop("pending_verify_business_id", None)
        return redirect(url_for("login"))

    code = (request.form.get("code") or "").strip()
    if not business_email_verify_code_matches(business_id, code):
        wait_seconds = email_verify_resend_wait_seconds(business_id, EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS)
        return render_template(
            "verify_pending.html", contact_email=business[4], resent=False, resend_wait_seconds=wait_seconds,
            error="Incorrect or expired code. Please try again.",
        )

    mark_email_verified(business_id)
    log_event("email_verified", business_id=business_id)
    session.pop("pending_verify_business_id", None)
    session["business_id"] = business_id
    session["business_name"] = business[1]
    return redirect(url_for("home"))


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
# Signup verification codes are short-lived compared to the email-change link above -
# they're meant to be read out of an inbox and typed back in within the same sitting, not
# saved for later, so a tight window limits how long a leaked/guessed code stays useful.
EMAIL_VERIFY_CODE_LIFETIME = timedelta(minutes=15)
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


def _verification_code_email_html(heading, intro_html, code, footer_note):
    """Same branded-card layout as _verification_email_html, but with the code itself
    displayed as the call to action instead of a clickable link - see
    send_verification_email's own docstring for why signup verification is a typed code
    rather than a link."""
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
              <h1 style="font-size:20px; color:#2b2013; margin:0 0 16px;">{heading}</h1>
              <p style="font-size:15px; line-height:1.6; color:#463824; margin:0 0 24px; text-align:left;">
                {intro_html}
              </p>
              <div style="display:inline-block; padding:16px 28px; margin:0 0 24px; background:#f6f1e7; border-radius:12px; font-size:32px; font-weight:700; letter-spacing:8px; color:#2b2013;">{code}</div>
              <p style="font-size:13.5px; line-height:1.6; color:#8a7a5f; margin:0 0 24px;">
                This code expires in 15 minutes.
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
    """Issues a fresh signup verification code and emails it. Shared by the web /register
    route, the mobile /api/register endpoint, and both places that resend it (login()
    below and /api/login) once an account already exists but hasn't confirmed yet.

    Unlike the email-change flow (send_email_change_verification, still a clicked link),
    signup verification is a typed code - the account isn't logged in anywhere yet for a
    link to usefully return to, and (per the mobile app) a link opens outside the app
    entirely, so a code the user types back in wherever they started is what lets both
    web and app continue in place.

    The code is saved and the account already exists in the DB before this is ever called
    - a broken send (Resend down, bad API key, network blip) must not turn into a 500 that
    leaves the business stuck (account created, no session, nothing to resend from). Caught
    and logged here instead: the caller's own redirect to verify_pending still happens, and
    that page's own "Resend" button (see /verify-pending/resend) covers a retry once
    whatever went wrong clears."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + EMAIL_VERIFY_CODE_LIFETIME
    save_email_verify_code(business_id, code, expires)

    try:
        send_email(
            recipient=recipient,
            subject="Your Camilius verification code",
            body=(
                f"Hi,\n\n"
                f"Welcome to Camilius! Use the code below to confirm '{business_name}'s email address "
                f"and finish setting up your account.\n\n"
                f"Your verification code (valid for 15 minutes): {code}\n\n"
                f"If you didn't create a Camilius account, you can safely ignore this email."
            ),
            business_name="Camilius",
            reply_to=os.getenv("ALERT_EMAIL") or recipient,
            html=_verification_code_email_html(
                heading="Confirm your email",
                intro_html=f"Welcome to Camilius! Enter this code to confirm <strong>{business_name}</strong>'s email address and finish setting up your account.",
                code=code,
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
PHONE_VERIFY_CODE_LIFETIME = timedelta(minutes=15)
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
    account's contact email. Used whenever send_sms.sms_configured() is False (the default
    - no Twilio credentials set, see .env.example) as well as anywhere the SMS send itself
    fails (see api.py's /api/verify-phone/send). Triggered only from the Profile screen's
    "Verify phone number" button, never automatically at registration - unlike the account
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


def send_phone_verification_sms(business_id, business_name, phone_number):
    """SMS counterpart to send_phone_verification_email() - only ever called once
    send_sms.sms_configured() has already been checked by the caller (api.py's
    /api/verify-phone/send). A typed code rather than a link: the phone itself is where
    the message lands, so (unlike the emailed link, opened wherever the account's contact
    email happens to be read) there's no separate "browser" involved - the user is already
    looking at the app that needs the code, same reasoning as the signup email code (see
    send_verification_email). Raises on a Twilio failure; the caller falls back to the
    emailed link rather than leaving the button spinning on a dead SMS attempt."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + PHONE_VERIFY_CODE_LIFETIME
    save_phone_verify_code(business_id, code, expires)

    send_sms(
        phone_number,
        f"Camilius: {code} is your phone verification code for '{business_name}'. Valid for 15 minutes.",
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
def _slug_taken(value, exclude_business_id=None):
    """Same (value, exclude_business_id=...) -> bool shape as username_taken/
    contact_email_taken/phone_number_taken above, so it slots into
    _AVAILABILITY_CHECKS unchanged - backs both the onboarding wizard's live web-address
    check (onboarding_website_address) and settings.html's own page-address field. An
    invalid format (or a reserved word - see pages.RESERVED_SLUGS) counts as "taken" too:
    the caller only needs a single yes/no on "can I use this", and the slug input's own
    pattern attribute already stops most invalid input from reaching here at all."""
    slug = pages.normalize_slug(value)
    if not pages.is_valid_slug(slug):
        return True
    existing = page_by_slug(slug)
    if existing is None:
        return False
    return exclude_business_id is None or existing[1] != exclude_business_id


_AVAILABILITY_CHECKS = {
    "username": username_taken,
    "contact_email": contact_email_taken,
    "phone_number": phone_number_taken,
    "slug": _slug_taken,
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
    # indices: 0 id, 1 name, 2 username, 4 contact_email, 8 working_hours, 9 price_list,
    # 10 faq, 11 phone_number, 14 pending_contact_email
    username = business[2]
    contact_email = business[4]
    pending_contact_email = business[14]
    pro_access = BETA_FREE_MODE or has_pro_access(business_id)
    # get_business_page returns a plain positional row (id, business_id, slug,
    # published, tagline, about, created_at, updated_at, widget_domain, theme,
    # custom_theme_json) or None - turned into a dict here, same as `business` is built
    # as a dict below, so settings.html can use page.slug/page.tagline/... instead of a
    # second set of magic indices.
    page_row = get_business_page(business_id)
    business_page = None if page_row is None else {
        "slug": page_row[2], "published": page_row[3], "tagline": page_row[4], "about": page_row[5],
        "widget_domain": page_row[8], "theme": page_row[9], "custom_theme_json": page_row[10],
    }
    page_error = request.args.get("page_error")

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
                page=business_page,
                page_error=None,
                page_url_prefix=request.url_root + "p/",
                theme_choices=list(themes.THEMES.items()),
                documents=business_documents.list_documents(business_id),
                media=business_media.list_media(business_id),
                max_gallery_images=business_media.MAX_GALLERY_IMAGES,
                business={
                    "name": name, "username": username, "contact_email": contact_email,
                    "pending_contact_email": pending_contact_email,
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
            page=business_page,
            page_error=None,
            page_url_prefix=request.url_root + "p/",
            theme_choices=list(themes.THEMES.items()),
            documents=business_documents.list_documents(business_id),
            media=business_media.list_media(business_id),
            max_gallery_images=business_media.MAX_GALLERY_IMAGES,
            business={
                "name": name, "username": username, "contact_email": contact_email,
                "pending_contact_email": pending_contact_email,
                                "working_hours": working_hours, "price_list": price_list, "faq": faq, "upsell_pairs": upsell_pairs,
            },
        )

    return render_template(
        "settings.html",
        error=None,
        message=request.args.get("message"),
        pro_access=pro_access,
        page=business_page,
        page_error=page_error,
        page_url_prefix=request.url_root + "p/",
        theme_choices=list(themes.THEMES.items()),
        documents=business_documents.list_documents(business_id),
        media=business_media.list_media(business_id),
        max_gallery_images=business_media.MAX_GALLERY_IMAGES,
        business={
            "name": business[1], "username": username, "contact_email": contact_email,
            "pending_contact_email": pending_contact_email,
                        "working_hours": business[8], "price_list": business[9], "faq": business[10],
            "upsell_pairs": get_upsell_pairs(business_id),
        },
    )


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
    owner_minutes = get_owner_reminder_minutes(business_id)
    owner_choices = _duration_choices_with_custom(REMINDER_LEAD_CHOICES, owner_minutes, lambda m: f"{m} min")
    return render_template(
        "app_settings.html",
        new_booking_notifications_enabled=is_new_booking_notifications_enabled(business_id),
        owner_reminders_enabled=is_owner_reminders_enabled(business_id),
        owner_reminder_minutes=owner_minutes,
        owner_reminder_choices=owner_choices,
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


# Appointment reminder toggle (Faza 9/12, Pro-only) - Starter-plan businesses can't turn
# it on even via a raw POST; the toggle is locked/hidden for them in app_settings.html.
@app.route("/settings/reminders", methods=["POST"])
@login_required
def update_reminders():
    business_id = session["business_id"]
    if BETA_FREE_MODE or has_pro_access(business_id):
        set_owner_reminders_enabled(business_id, bool(request.form.get("owner_reminders_enabled")))
        owner_minutes = _parse_reminder_minutes(
            request.form.get("owner_reminder_minutes"), request.form.get("owner_reminder_minutes_custom"), 60)
        set_owner_reminder_minutes(business_id, owner_minutes)
    return redirect(url_for("app_settings"))


# Saves the business's own hosted public page (/p/<slug>) - a separate, self-contained
# <form> in settings.html rather than folded into the big #settings-form/settings() POST
# above, on purpose: that form already handles account/hours/price/faq/password/upsell,
# and this has its own distinct failure mode (slug taken/invalid) that doesn't fit its
# _rerender(error) shape. Same "standalone <form> after #settings-form's closing tag,
# posting to its own small dedicated route" pattern as update_reminders above and the
# upgrade-to-pro-free form in settings.html.
@app.route("/profile/page", methods=["POST"])
@login_required
def save_business_page():
    business_id = session["business_id"]
    slug = pages.normalize_slug(request.form.get("slug", ""))
    tagline = (request.form.get("tagline") or "").strip()[:200] or None
    about = (request.form.get("about") or "").strip()[:2000] or None
    published = bool(request.form.get("published"))
    # F5: the one external origin allowed to embed this business's /w/<slug> widget in
    # an iframe elsewhere (see pages.normalize_widget_domain/app.py's
    # set_security_headers) - None if the field was left blank or didn't parse to a
    # usable host, same "no domain approved yet" state a business that's never touched
    # this field is already in.
    widget_domain = pages.normalize_widget_domain(request.form.get("widget_domain"))
    # None (rather than themes.DEFAULT_THEME_ID) for anything not a recognized theme id -
    # upsert_business_page() treats None as "leave whatever theme is already saved
    # alone", which matters for a raw/malformed POST to this route (no known theme <select>
    # options are ever anything else, but this is the boundary that actually enforces it).
    # themes.CUSTOM_THEME_ID is the one exception - settings.html's "Suggest AI designs"
    # picker (see ai_theme.py) posts that alongside a custom_theme_json hidden field
    # rather than one of the fixed THEMES ids.
    theme = request.form.get("theme")
    custom_theme_json = None
    if theme == themes.CUSTOM_THEME_ID:
        custom_theme_json = request.form.get("custom_theme_json") or None
        if not custom_theme_json:
            theme = None
    elif theme not in themes.THEMES:
        theme = None

    # Re-renders settings.html in place (rather than redirecting to a fresh GET of
    # /profile) so a failed save doesn't wipe out what the business just typed - a bare
    # redirect only has page_error to pass along, so slug/tagline/about/widget_domain/
    # theme would all snap back to whatever (if anything) was last actually saved,
    # leaving the "Web address" field looking empty right above an error insisting it's
    # invalid. Same "re-render with the submitted values, don't redirect" shape as
    # settings()'s own _rerender(error) above, just scoped to this card's fields instead
    # of the whole profile.
    def _rerender_page_error(page_error):
        business = business_by_id(business_id)
        page_row = get_business_page(business_id)
        return render_template(
            "settings.html",
            error=None,
            message=None,
            pro_access=BETA_FREE_MODE or has_pro_access(business_id),
            page={
                "slug": slug, "published": published, "tagline": tagline, "about": about,
                "widget_domain": widget_domain,
                "theme": theme or (page_row[9] if page_row else themes.DEFAULT_THEME_ID),
                "custom_theme_json": custom_theme_json or (page_row[10] if page_row else None),
            },
            page_error=page_error,
            page_url_prefix=request.url_root + "p/",
            theme_choices=list(themes.THEMES.items()),
            documents=business_documents.list_documents(business_id),
            media=business_media.list_media(business_id),
            max_gallery_images=business_media.MAX_GALLERY_IMAGES,
            business={
                "name": business[1], "username": business[2], "contact_email": business[4],
                "pending_contact_email": business[14],
                "working_hours": business[8], "price_list": business[9], "faq": business[10],
                "upsell_pairs": get_upsell_pairs(business_id),
            },
        )

    # A valid slug is required even to save a draft (published=False) - there is nothing
    # useful to store without one, and requiring it up front means a business never ends
    # up with a page row that publishing later would fail on for a reason it can't see
    # from the publish toggle alone.
    if not pages.is_valid_slug(slug):
        return _rerender_page_error("Please choose a web address using only lowercase letters, numbers, and hyphens.")

    try:
        upsert_business_page(business_id, slug, published, tagline, about, widget_domain, theme, custom_theme_json)
    except UniqueViolation:
        return _rerender_page_error("That web address is already taken - please choose another.")

    log_event("business_page_saved", business_id=business_id)
    return redirect(url_for("settings", message="Your page has been saved."))


# AJAX-only (see settings.html's "Suggest AI designs" button/script) - lets a business
# re-run ai_theme.generate_theme_options() any time from Settings, not just once during
# onboarding (see onboarding_website_design() for that first-run path). Returns theme
# dicts as plain JSON; nothing is saved here, same "review before it's written" shape as
# generate_business_page() above - the business still has to pick one and hit Save page.
@app.route("/profile/suggest-themes", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def suggest_business_themes():
    business_id = session["business_id"]
    description = (request.form.get("description") or "").strip()
    if not description:
        return jsonify(error="Describe your business first so the AI has something to design from."), 400

    document_text = business_documents.combined_document_text(business_id)
    options = ai_theme.generate_theme_options(description, document_text=document_text)
    if not options:
        return jsonify(error="Couldn't come up with designs from that - please try describing your business differently."), 400

    log_event("theme_suggestions_generated", business_id=business_id)
    return jsonify(themes=options)


# Drafts tagline/about/hours/prices/faq from a short owner-written description
# (page_generator.py) and re-renders settings.html with them pre-filled - same
# "re-render the form with different values, don't redirect" shape as settings()'s own
# _rerender(error). Deliberately does NOT call update_business()/upsert_business_page():
# working_hours/price_list/faq are required fields at registration, so a business
# already has real content in them the moment it exists - writing a draft straight to
# the database here could silently change what the AI tells customers on other channels
# before the owner has looked at a single word of it. The owner reviews the pre-filled
# form and clicks the existing "Save changes"/"Save page" buttons themselves.
@app.route("/profile/generate-page", methods=["POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def generate_business_page():
    business_id = session["business_id"]
    business = business_by_id(business_id)
    username = business[2]
    contact_email = business[4]
    pending_contact_email = business[14]
    pro_access = BETA_FREE_MODE or has_pro_access(business_id)
    page_row = get_business_page(business_id)
    saved_page = None if page_row is None else {
        "slug": page_row[2], "published": page_row[3], "tagline": page_row[4], "about": page_row[5],
        "widget_domain": page_row[8], "theme": page_row[9], "custom_theme_json": page_row[10],
    }

    description = (request.form.get("description") or "").strip()
    draft = None
    if not description:
        generate_error = "Describe your business first so the AI has something to draft from."
    else:
        try:
            draft = generate_page_draft(description, document_text=business_documents.combined_document_text(business_id))
            generate_error = None
        except ValueError:
            generate_error = "Couldn't generate a draft from that - please try describing your business differently."

    if draft is None:
        business_view = {
            "name": business[1], "username": username, "contact_email": contact_email,
            "pending_contact_email": pending_contact_email,
                        "working_hours": business[8], "price_list": business[9], "faq": business[10],
            "upsell_pairs": get_upsell_pairs(business_id),
        }
        page_view = saved_page
    else:
        business_view = {
            "name": business[1], "username": username, "contact_email": contact_email,
            "pending_contact_email": pending_contact_email,
                        "working_hours": format_working_hours(draft["hours"]),
            "price_list": format_price_list(draft["services"]),
            "faq": format_faq(draft["faq"]),
            "upsell_pairs": get_upsell_pairs(business_id),
        }
        # Keeps whatever slug/published/widget_domain state is already saved (a
        # generated draft has no opinion on the web address, whether it's live, or the
        # widget) and only overwrites the presentation fields the AI actually drafted.
        page_view = {
            "slug": saved_page["slug"] if saved_page else "",
            "published": saved_page["published"] if saved_page else False,
            "widget_domain": saved_page["widget_domain"] if saved_page else None,
            "theme": saved_page["theme"] if saved_page else themes.DEFAULT_THEME_ID,
            "custom_theme_json": saved_page["custom_theme_json"] if saved_page else None,
            "tagline": draft["tagline"],
            "about": draft["about"],
        }

    return render_template(
        "settings.html",
        error=None,
        message=None,
        pro_access=pro_access,
        page=page_view,
        page_error=None,
        generate_error=generate_error,
        generate_description=description,
        page_url_prefix=request.url_root + "p/",
        theme_choices=list(themes.THEMES.items()),
        documents=business_documents.list_documents(business_id),
        media=business_media.list_media(business_id),
        max_gallery_images=business_media.MAX_GALLERY_IMAGES,
        business=business_view,
    )


# --- Onboarding wizard (Faza 13) - the mandatory "build your website" flow a business is
# sent through right after email verification (see onboarding_required above), before it
# can reach the dashboard at all. Five steps, each its own route/template, state carried
# forward in session[_ONBOARDING_SESSION_KEY] between them rather than as hidden form
# fields (unlike the delete-account wizard's own three steps) - the AI draft this stashes
# (tagline/about/hours/prices/faq) is too much to round-trip through hidden inputs on
# every step cleanly. The media step (Faza 14) is the one exception - uploads go straight
# to business_media via this business's real business_id, nothing about them is staged in
# the draft. Cleared the moment step 5 actually publishes (or never populated at all for a
# grandfathered pre-existing business - see onboarding_completed).
_ONBOARDING_SESSION_KEY = "onboarding_draft"


def _slugify_business_name(name):
    """A starting suggestion for onboarding_website_address's slug field, not a
    validated result on its own - the caller still runs this through
    pages.is_valid_slug/page_by_slug before trusting it, same as any other slug. Lowercases,
    collapses anything that isn't a-z0-9 into single hyphens, and trims to the same
    50-char ceiling pages._SLUG_RE enforces. Falls back to "my-business" if the name has
    no ASCII letters/digits at all (e.g. a name written entirely in Cyrillic)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:50].strip("-")
    return slug or "my-business"


@app.route("/onboarding/website", methods=["GET", "POST"])
@login_required
def onboarding_website_describe():
    business_id = session["business_id"]
    if onboarding_completed(business_id):
        return redirect(url_for("home"))

    draft = session.get(_ONBOARDING_SESSION_KEY) or {}
    description = draft.get("description", "")
    error = None

    if request.method == "POST":
        description = (request.form.get("description") or "").strip()
        if not description:
            error = "Describe your business first so the AI has something to draft from."
        else:
            try:
                document_text = business_documents.combined_document_text(business_id)
                generated = generate_page_draft(description, document_text=document_text)
            except ValueError:
                error = "Couldn't generate a draft from that - please try describing your business differently."
            else:
                # Best-effort - see ai_theme.generate_theme_options's own docstring: an
                # empty list here just means onboarding_website_design() shows only the
                # classic THEMES presets, never a hard failure of this step.
                ai_theme_options = ai_theme.generate_theme_options(description, document_text=document_text)
                session[_ONBOARDING_SESSION_KEY] = {
                    "description": description,
                    "tagline": generated["tagline"],
                    "about": generated["about"],
                    "working_hours": format_working_hours(generated["hours"]),
                    "price_list": format_price_list(generated["services"]),
                    "faq": format_faq(generated["faq"]),
                    "ai_theme_options": ai_theme_options,
                }
                log_event("onboarding_draft_generated", business_id=business_id)
                return redirect(url_for("onboarding_website_design"))

    return render_template(
        "onboarding_describe.html", description=description, error=error,
        documents=business_documents.list_documents(business_id),
    )


# Voice input for the step above: an owner records a short spoken description instead of
# typing one, this transcribes it (OpenAI Whisper) and hands the text back to the browser
# to drop into the same textarea onboarding_website_describe() reads - never generates a
# page draft directly, so the owner always reviews/edits the transcript before it's used,
# same as anything they'd typed themselves (speech-to-text can mishear things). Read
# fully into memory and never written to disk or the database - only the resulting text
# is ever kept, exactly like the typed description already is.
@app.route("/api/onboarding/voice-transcribe", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def onboarding_voice_transcribe():
    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        return jsonify(error="No recording received."), 400

    # Read one byte past the cap so an oversized upload is caught here instead of being
    # silently truncated mid-audio and sent to Whisper as a corrupt/partial file.
    audio_bytes = audio.read(MAX_VOICE_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_VOICE_AUDIO_BYTES:
        return jsonify(error="That recording is too long - keep it under a minute or so."), 400

    try:
        text = transcribe_voice_description(audio_bytes, audio.filename, audio.mimetype)
    except ValueError:
        return jsonify(error="Couldn't make sense of that recording - please try again or type your description instead."), 400
    finally:
        # Drop the only in-process reference so the raw audio bytes are eligible for
        # garbage collection immediately, rather than lingering for the rest of the
        # request - belt-and-suspenders alongside "never written to disk" above.
        del audio_bytes

    log_event("onboarding_voice_transcribed", business_id=session["business_id"])
    return jsonify(text=text)


# Website media (logo/gallery photos/hero video) - see business_media.py for the actual
# upload/replace/delete rules and storage.py for the underlying Cloudinary calls. Used
# by both the onboarding wizard's media step (onboarding_media.html) and settings.html
# afterward, so a business can keep managing photos once onboarding is done - unlike the
# rest of onboarding_website_*, this isn't gated behind onboarding_completed().
@app.route("/api/onboarding/media", methods=["GET", "POST"])
@login_required
@limiter.limit("60 per hour")
def onboarding_media():
    business_id = session["business_id"]
    if request.method == "GET":
        return jsonify(media=business_media.list_media(business_id))

    role = request.form.get("role", "")
    file_storage = request.files.get("file")
    try:
        item = business_media.upload_media(business_id, role, file_storage)
    except business_media.MediaError as error:
        return jsonify(error=str(error)), error.status

    log_event("onboarding_media_uploaded", business_id=business_id)
    return jsonify(**item)


@app.route("/api/onboarding/media/<int:media_id>", methods=["DELETE"])
@login_required
def onboarding_media_delete(media_id):
    business_id = session["business_id"]
    try:
        business_media.delete_media(business_id, media_id)
    except business_media.MediaError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(deleted=True)


# Source documents (PDF/Word - e.g. a menu, price list, or brochure) uploaded alongside
# the typed description, so generate_page_draft() has real facts to draw prices/services
# from instead of the owner retyping everything - see business_documents.py for the
# upload/extract rules and document_extract.py for the text extraction itself. Same
# "reachable outside onboarding too" shape as /api/onboarding/media above.
@app.route("/api/onboarding/document", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per hour")
def onboarding_document():
    business_id = session["business_id"]
    if request.method == "GET":
        return jsonify(documents=business_documents.list_documents(business_id))

    file_storage = request.files.get("file")
    try:
        item = business_documents.upload_document(business_id, file_storage)
    except business_documents.DocumentError as error:
        return jsonify(error=str(error)), error.status

    log_event("onboarding_document_uploaded", business_id=business_id)
    return jsonify(**item)


@app.route("/api/onboarding/document/<int:document_id>", methods=["DELETE"])
@login_required
def onboarding_document_delete(document_id):
    business_id = session["business_id"]
    try:
        business_documents.delete_document(business_id, document_id)
    except business_documents.DocumentError as error:
        return jsonify(error=str(error)), error.status
    return jsonify(deleted=True)


@app.route("/onboarding/website/design", methods=["GET", "POST"])
@login_required
def onboarding_website_design():
    business_id = session["business_id"]
    if onboarding_completed(business_id):
        return redirect(url_for("home"))
    draft = session.get(_ONBOARDING_SESSION_KEY)
    if draft is None:
        return redirect(url_for("onboarding_website_describe"))

    ai_options = draft.get("ai_theme_options") or []

    error = None
    if request.method == "POST":
        theme_id = request.form.get("theme") or ""
        if theme_id.startswith("ai:"):
            try:
                ai_index = int(theme_id.split(":", 1)[1])
            except ValueError:
                ai_index = -1
            if 0 <= ai_index < len(ai_options):
                draft["theme"] = themes.CUSTOM_THEME_ID
                draft["custom_theme_json"] = json.dumps(ai_options[ai_index])
                draft["ai_theme_index"] = ai_index
                session[_ONBOARDING_SESSION_KEY] = draft
                return redirect(url_for("onboarding_website_address"))
            error = "Please choose a design."
        elif theme_id in themes.THEMES:
            draft["theme"] = theme_id
            draft.pop("custom_theme_json", None)
            draft.pop("ai_theme_index", None)
            session[_ONBOARDING_SESSION_KEY] = draft
            return redirect(url_for("onboarding_website_address"))
        else:
            error = "Please choose a design."

    selected_theme = f"ai:{draft['ai_theme_index']}" if draft.get("theme") == themes.CUSTOM_THEME_ID and "ai_theme_index" in draft else draft.get("theme")
    return render_template(
        "onboarding_design.html",
        draft=draft,
        ai_theme_choices=list(enumerate(ai_options)),
        theme_choices=list(themes.THEMES.items()),
        selected_theme=selected_theme,
        error=error,
    )


@app.route("/onboarding/website/address", methods=["GET", "POST"])
@login_required
def onboarding_website_address():
    business_id = session["business_id"]
    if onboarding_completed(business_id):
        return redirect(url_for("home"))
    draft = session.get(_ONBOARDING_SESSION_KEY)
    if draft is None or "theme" not in draft:
        return redirect(url_for("onboarding_website_describe"))

    business = business_by_id(business_id)
    slug_value = draft.get("slug") or _slugify_business_name(business[1])
    error = None

    if request.method == "POST":
        slug_value = pages.normalize_slug(request.form.get("slug", ""))
        if not pages.is_valid_slug(slug_value):
            error = "Please choose a web address using only lowercase letters, numbers, and hyphens."
        elif page_by_slug(slug_value) is not None:
            error = "That web address is already taken - please choose another."
        else:
            draft["slug"] = slug_value
            session[_ONBOARDING_SESSION_KEY] = draft
            return redirect(url_for("onboarding_website_media"))

    return render_template(
        "onboarding_address.html",
        slug_value=slug_value,
        page_url_prefix=request.url_root + "p/",
        error=error,
    )


# Photos/video step - logo, a gallery of shots of the product/space, one hero video (see
# business_media.py). Uploads happen immediately against this business's real business_id
# via /api/onboarding/media (AJAX, see onboarding_media.html), not staged in the session
# draft the way the earlier text steps are - a business_media row is harmless to have
# even if the wizard is abandoned before Publish, unlike the businesses-table edits the
# review step's own POST makes. This step is entirely optional: Continue always proceeds,
# whether or not anything was uploaded.
@app.route("/onboarding/website/media", methods=["GET", "POST"])
@login_required
def onboarding_website_media():
    business_id = session["business_id"]
    if onboarding_completed(business_id):
        return redirect(url_for("home"))
    draft = session.get(_ONBOARDING_SESSION_KEY)
    if draft is None or "slug" not in draft:
        return redirect(url_for("onboarding_website_describe"))

    if request.method == "POST":
        return redirect(url_for("onboarding_website_review"))

    return render_template(
        "onboarding_media.html",
        draft=draft,
        media=business_media.list_media(business_id),
        max_gallery_images=business_media.MAX_GALLERY_IMAGES,
    )


@app.route("/onboarding/website/review", methods=["GET", "POST"])
@login_required
def onboarding_website_review():
    business_id = session["business_id"]
    if onboarding_completed(business_id):
        return redirect(url_for("home"))
    draft = session.get(_ONBOARDING_SESSION_KEY)
    if draft is None or "slug" not in draft:
        return redirect(url_for("onboarding_website_describe"))

    if request.method == "POST":
        business = business_by_id(business_id)
        # phone_number (business[11]) carried through unchanged, same as settings()'s
        # own update_business() call - this step never touches it.
        update_business(
            business_id, business[1], business[2], business[4], business[11],
            draft["working_hours"], draft["price_list"], draft["faq"],
        )
        try:
            upsert_business_page(
                business_id, draft["slug"], True, draft["tagline"], draft["about"],
                None, draft["theme"], draft.get("custom_theme_json"),
            )
        except UniqueViolation:
            # Someone else grabbed this slug in the gap between step 3's check and this
            # submit - bounce back to re-pick rather than 500ing on Publish.
            return redirect(url_for("onboarding_website_address"))
        mark_onboarding_completed(business_id)
        log_event("onboarding_completed", business_id=business_id)
        session.pop(_ONBOARDING_SESSION_KEY, None)
        return redirect(url_for("home", onboarding_done=1))

    return render_template(
        "onboarding_review.html",
        draft=draft,
        theme=themes.resolve_theme(draft.get("theme"), draft.get("custom_theme_json")),
        page_url_prefix=request.url_root + "p/",
    )


# Live preview iframe backing both onboarding_website_design (swaps ?theme= per card the
# visitor is looking at) and onboarding_website_review (the chosen theme, final check
# before Publish) - renders the exact same public_page.html a real visitor would see,
# fed from the in-session draft instead of the database since nothing is saved yet at
# either step. 404s (rather than rendering something empty/broken) once there's no draft
# to preview - matches how every other onboarding_website_* route treats a missing one.
@app.route("/onboarding/website/preview")
@login_required
def onboarding_website_preview():
    business_id = session["business_id"]
    draft = session.get(_ONBOARDING_SESSION_KEY)
    if draft is None:
        abort(404)
    business = business_by_id(business_id)
    requested_theme = request.args.get("theme") or draft.get("theme")
    if requested_theme and requested_theme.startswith("ai:"):
        ai_options = draft.get("ai_theme_options") or []
        try:
            ai_index = int(requested_theme.split(":", 1)[1])
        except ValueError:
            ai_index = -1
        theme = ai_options[ai_index] if 0 <= ai_index < len(ai_options) else themes.theme_or_default(None)
    else:
        theme = themes.resolve_theme(requested_theme, draft.get("custom_theme_json"))
    media = business_media.media_for_page(business_id)
    return render_template(
        "public_page.html",
        business_name=business[1],
        working_hours=draft["working_hours"],
        price_list=draft["price_list"],
        faq=draft["faq"],
        tagline=draft["tagline"],
        about=draft["about"],
        opening_hours_schema=[],
        opening_hours_display=pages.opening_hours_display(draft["working_hours"]),
        page_url="",
        chat_url="",
        slug="preview",
        theme=theme,
        preview=True,
        show_badge=False,
        logo_url=media["logo_url"],
        gallery_urls=media["gallery_urls"],
        hero_video_url=media["hero_video_url"],
    )


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
    return redirect(url_for("login", account_deleted=1))


def business_export_data(business_id):
    """Shared by the web /export-data route (session auth) and the mobile app's
    token-gated /export-data/<token> (see api.py's /api/export-data-link) - a
    self-serve GDPR-style export of a business's own data. Deliberately excludes
    secrets (password hash) even though those fields aren't otherwise sensitive
    to the business itself, since there's no
    reason to ever put them in a downloadable file."""
    business = business_by_id(business_id)
    conversations = conversations_for_business(business_id)
    return {
        "business": {
            "name": business[1],
            "username": business[2],
            "contact_email": business[4],
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
# instead, requested first through /api/export-data-link.
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
# pages instead of relying only on their own crawl schedule against stale cached copies.
@app.route("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {url_for('sitemap_xml', _external=True)}",
    ])
    return Response(body, mimetype="text/plain")


# Renders a business's own hosted public page - the delivery vehicle/SEO surface a
# business gets automatically alongside the AI that answers their customers (see
# save_business_page() above and pages.py). page_by_slug() returns an unpublished page
# too (see its own docstring), so "no such slug" and "not published yet" 404 identically
# here rather than leaking which one it was.
@app.route("/p/<slug>")
def public_page(slug):
    page = page_by_slug(slug)
    if page is None or not page[3]:
        abort(404)
    business = business_by_id(page[1])
    if business is None:
        abort(404)
    # Booking is only ever offered where the AI chat tool itself is allowed to actually
    # book (see ai_replies.handle_incoming_message's identical has_pro_access/
    # BETA_FREE_MODE check) and where there's at least one open day to show a slot for -
    # a visible "Book an appointment" section that leads to a chat reply of "sorry, I
    # can't book that" would be worse than no section at all.
    booking_enabled = (
        (BETA_FREE_MODE or has_pro_access(page[1]))
        and any(booking.parse_working_hours(business[8]).values())
    )
    media = business_media.media_for_page(page[1])
    return render_template(
        "public_page.html",
        business_name=business[1],
        working_hours=business[8],
        price_list=business[9],
        faq=business[10],
        tagline=page[4],
        about=page[5],
        opening_hours_schema=pages.opening_hours_schema(business[8]),
        opening_hours_display=pages.opening_hours_display(business[8]),
        page_url=url_for("public_page", slug=slug, _external=True),
        chat_url=url_for("public_page_chat", slug=slug),
        slug=slug,
        theme=themes.resolve_theme(page[9], page[10]),
        preview=False,
        booking_enabled=booking_enabled,
        availability_url=url_for("public_page_availability", slug=slug) if booking_enabled else None,
        # F4: the "Powered by Camilius" footer disappears once this business has any
        # active access - including its own free trial, same as has_pro_access() already
        # treats a self-serve trial as full access, not just a paid plan. One source of
        # truth (effective_subscription_active) rather than a second "is this really
        # paid, not just trialing" check that could quietly drift from it.
        show_badge=not effective_subscription_active(page[1]),
        logo_url=media["logo_url"],
        gallery_urls=media["gallery_urls"],
        hero_video_url=media["hero_video_url"],
    )


# How many upcoming days public_page_availability looks across for a free slot - a
# visitor scanning for "any time this week/next" needs more runway than the AI chat's own
# find_next_free_slots (which stops as soon as it has 2 alternatives to offer), but this
# is still a bounded read, same spirit as that function's own search_days cap.
PAGE_AVAILABILITY_DAYS = 14


# Read-only free/busy for the public page's own "Book an appointment" calendar (see
# public_page.html) - deliberately NOT a booking endpoint itself. The actual booking
# transaction (name, notifications, upsell, double-book protection) stays entirely inside
# the AI chat pipeline (booking.attempt_ai_booking via ai_replies/public_page_chat) - this
# just lets a visitor see open times before they ever open the chat panel, and tapping one
# hands the exact same request to that same chat pipeline as a plain typed message. Same
# has_pro_access/BETA_FREE_MODE gate as the chat tool itself, so this never advertises
# slots the business can't actually have the AI book.
@app.route("/p/<slug>/availability")
@limiter.limit("30 per minute")
def public_page_availability(slug):
    page = page_by_slug(slug)
    if page is None or not page[3]:
        abort(404)
    business_id = page[1]
    business = business_by_id(business_id)
    if business is None or not (BETA_FREE_MODE or has_pro_access(business_id)):
        abort(404)

    working_hours = business[8]
    tz = booking.tz_for(business)
    slot_minutes = get_slot_duration(business_id)
    now = booking.now_local(tz)

    days = []
    for offset in range(PAGE_AVAILABILITY_DAYS):
        d = now.date() + timedelta(days=offset)
        if not booking.is_open_on(working_hours, d):
            continue
        free = [s for s in booking.free_slots_for_day(business_id, working_hours, slot_minutes, d, tz) if s >= now]
        if not free:
            continue
        days.append({
            "date": d.isoformat(),
            "label": d.strftime("%a, %b %d"),
            "slots": [s.strftime("%H:%M") for s in free],
        })

    return jsonify(days=days)


# How many page-chat messages one anonymous visitor session (the browser's own
# localStorage token - see public_page.html's script block - not an IP, which a visitor
# on mobile data can change mid-conversation) may send in a day. This is one of three
# cost-protection layers on public_page_chat() below, the first surface reachable by
# anyone on the internet without an account: Flask-Limiter's per-IP and per-slug limits
# just below throttle bursts but reset on every deploy (extensions.py's limiter has no
# storage_uri - in-memory only); replies_today_count()'s existing business-wide daily cap
# (still enforced inside ai_replies.handle_incoming_message for every channel, web
# included) bounds the aggregate worst case; this bounds what a single token can do
# regardless of either, and - being a database count, not a limiter count - survives a
# redeploy exactly like the visitor's own localStorage token does. Generous enough for a
# real multi-message booking conversation, bounded against a replayed/scripted token.
PAGE_CHAT_SESSION_DAILY_LIMIT = 40

# session_id is never trusted for anything but keying its own rate limit/history/booking
# lookups (see ai_replies._SENDER_KEY_KWARG's "web" entry) - this only rejects obviously
# wrong input (missing, empty, absurdly long), not full UUID validation.
def _valid_page_chat_session_id(value):
    return isinstance(value, str) and 8 <= len(value) <= 128


@app.route("/p/<slug>/chat", methods=["POST"])
@csrf.exempt  # anonymous page visitor, no session/CSRF token of ours - rate-limited below instead
@limiter.limit("20 per hour")  # per IP (Flask-Limiter's default key)
@limiter.limit("300 per hour", key_func=lambda: request.view_args.get("slug", ""))  # per business page, across all visitors
def public_page_chat(slug):
    page = page_by_slug(slug)
    if page is None or not page[3]:
        abort(404)
    business_id = page[1]
    business = business_by_id(business_id)
    if business is None:
        abort(404)

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:2000]
    session_id = data.get("session_id")
    if not message or not _valid_page_chat_session_id(session_id):
        abort(400)

    if page_chat_session_messages_today(business_id, session_id) >= PAGE_CHAT_SESSION_DAILY_LIMIT:
        return jsonify(reply="I've answered a lot of questions today - please contact us directly for anything else.")

    def resolve_sender():
        # No real name to resolve - sender_display only
        # needs to be unique per session, since previous_conversations_with() (this
        # visitor's own chat memory) matches on it exactly. The session token's own tail
        # already guarantees that; a human-readable label is just for the dashboard.
        return session_id, f"Web visitor #{session_id[-6:]}", None

    def deliver(reply_text):
        # "Delivering" a page-chat reply IS this request's own HTTP response (see
        # reply_text pulled off the result below) - there's no separate channel API to
        # push it through, so this has nothing to do.
        pass

    result = ai_replies.handle_incoming_message(
        business, business_id, "web", None, message, resolve_sender, deliver, log_prefix="[page-chat]",
    )
    if result is None:
        # Gated (subscription inactive, or replies_today_count's business-wide daily cap
        # already reached by this or any other channel) - still a normal chat reply as
        # far as the widget is concerned, not an error it needs to handle specially.
        return jsonify(reply="I'm not able to answer right now - please try again later or contact us directly.")
    return jsonify(reply=result["reply_text"])


# F5: the embeddable widget for a business that already has its own site - a small
# <iframe> (see widget_frame.html) loaded by the equally small loader script at
# GET /widget.js below, both reusing the exact same /p/<slug>/chat backend the hosted
# page's own inline widget already calls (see public_page_chat above - it doesn't care
# whether the request came from our own page or someone else's iframe). page_by_slug()
# returns an unpublished page too, same "no such slug" and "not published yet" 404
# identically" reasoning as public_page() above.
@app.route("/w/<slug>")
def widget_frame(slug):
    page = page_by_slug(slug)
    if page is None or not page[3]:
        abort(404)
    business = business_by_id(page[1])
    if business is None:
        abort(404)
    # Read by set_security_headers below (via flask.g, not a second DB lookup) to build
    # this response's own CSP frame-ancestors - the actual mechanism that decides
    # whether any site is allowed to embed this in an iframe at all. None (no domain
    # approved yet) still renders fine on a direct visit; it just can't be embedded
    # anywhere until the business sets one in Settings.
    g.widget_frame_ancestor = page[8]
    return render_template(
        "widget_frame.html",
        business_name=business[1],
        chat_url=url_for("public_page_chat", slug=slug),
        slug=slug,
        widget_domain=page[8],
        theme=themes.resolve_theme(page[9], page[10]),
    )


# The tiny script a business pastes onto their OWN site's HTML to get the widget:
# <script src=".../widget.js?slug=their-slug" async></script>. Reads its own slug back
# off document.currentScript's URL (no server-side templating needed - one identical
# script works for every business), creates one small fixed-position iframe pointing at
# /w/<slug> above, and resizes that iframe between a small closed "bubble" footprint and
# a larger open "panel" footprint on postMessage from inside it - the parent page's own
# layout is never touched beyond that one positioned element.
_WIDGET_LOADER_JS = """
(function () {
    var script = document.currentScript;
    if (!script) return;
    var src = new URL(script.src, window.location.href);
    var slug = src.searchParams.get("slug");
    if (!slug) return;
    var origin = src.origin;

    var iframe = document.createElement("iframe");
    iframe.src = origin + "/w/" + encodeURIComponent(slug);
    iframe.title = "Chat";
    iframe.style.cssText = "position:fixed;bottom:20px;right:20px;width:84px;height:84px;"
        + "border:none;z-index:2147483000;background:transparent;"
        + "transition:width .2s ease,height .2s ease;colorScheme:light dark;";
    document.body.appendChild(iframe);

    window.addEventListener("message", function (event) {
        if (event.origin !== origin || !event.data || event.data.source !== "camilius-widget") return;
        if (event.data.open) {
            iframe.style.width = Math.min(380, window.innerWidth - 32) + "px";
            iframe.style.height = Math.min(620, window.innerHeight - 32) + "px";
        } else {
            iframe.style.width = "84px";
            iframe.style.height = "84px";
        }
    });
})();
"""


@app.route("/widget.js")
def widget_loader_js():
    return Response(_WIDGET_LOADER_JS, mimetype="application/javascript")


@app.route("/sitemap.xml")
def sitemap_xml():
    static_pages = ["home", "login", "register", "terms", "privacy", "download"]
    urls = "".join(f"<url><loc>{url_for(page, _external=True)}</loc></url>" for page in static_pages)
    # <lastmod> (absent from the static pages above - none of them track an edit
    # timestamp) tells a crawler whether it's worth re-fetching a page it already has;
    # a plain date is valid per the sitemap spec, no need for a full timestamp.
    page_urls = "".join(
        f"<url><loc>{url_for('public_page', slug=slug, _external=True)}</loc><lastmod>{updated_at.date().isoformat()}</lastmod></url>"
        for slug, updated_at in published_page_slugs()
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}{page_urls}</urlset>'
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
    return render_template("download.html", apk_version=APK_VERSION)


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


# Founder-only lookup/cleanup tool - finds EVERY business, including abandoned
# test/onboarding-incomplete accounts, and can delete one by id. Same secret-token
# gating as every other /admin route.
@app.route("/admin/businesses")
def admin_businesses():
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    search = (request.args.get("q") or "").strip().lower()
    result = []
    for business in all_businesses_unfiltered():
        # indices: 0 id, 1 name, 2 username, 4 contact_email, 11 phone_number,
        # 12 email_verified, 13 phone_verified, 14 pending_contact_email. subscription_status
        # and created_at are the two extras all_businesses_unfiltered() appends AFTER the
        # business columns, so they're read from the END (-2, -1) rather than by absolute
        # index: every time _BUSINESS_COLUMNS grows, an absolute index here silently
        # points at the wrong column, which is exactly how this route broke once before.
        if search and search not in business[1].lower() and search not in business[2].lower() and search not in business[4].lower():
            continue
        result.append({
            "id": business[0],
            "name": business[1],
            "username": business[2],
            "contact_email": business[4],
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


# Founder-only manual comp: flips a business straight to an active Pro plan without a
# checkout, for cases (support, testing, the founder's own account) that shouldn't have
# to go through real billing. Same token gating as every other /admin route above.
@app.route("/admin/businesses/<int:business_id>/grant-pro", methods=["POST"])
@csrf.exempt  # admin tooling authenticated by token (below), not a browser session
def admin_grant_pro(business_id):
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        abort(404)

    business = business_by_id(business_id)
    if business is None:
        abort(404)
    grant_free_pro_access(business_id)
    return jsonify(granted=True, business_id=business_id, name=business[1])


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


DAILY_REPLY_LIMIT = ai_replies.DAILY_REPLY_LIMIT  # single source of truth is ai_replies.py, which actually enforces it


# The true post-login landing page (Faza 15) - every existing redirect-to-home() call
# site (login, register/verify, onboarding's final Publish step, subscription success,
# etc.) already points at url_for("home"), i.e. "/", so keeping this route's name and
# path unchanged and moving the old conversation-inbox view to its own /conversations
# route (conversations_home() below) means all of those keep landing exactly where a
# business should land first: an overview of the website Camilius built for them, not a
# raw message inbox - matching the product's actual pitch (see landing.html/intro.html)
# instead of undercutting it. onboarding_required/subscription_required stay stacked here
# only, same "single choke point every logged-in path passes through first" reasoning
# their own docstrings already describe - conversations_home() (and every other
# @login_required-only route like /schedule, /settings) relies on a visitor having
# already passed through here at least once in the session, same as before this split.
@app.route("/")
@onboarding_required
@subscription_required
def home():
    # Logged-out visitors see the public landing page (marketing + call to register)
    # instead of a redirect to login. Logged-in visitors get their website overview.
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

    business_id = session["business_id"]
    page_row = get_business_page(business_id)
    page = None if page_row is None else {
        "slug": page_row[2], "published": page_row[3], "tagline": page_row[4], "about": page_row[5],
        "theme": page_row[9],
    }
    theme = themes.resolve_theme(page_row[9], page_row[10]) if page_row else themes.theme_or_default(None)

    tz = booking.tz_for(business)
    now = booking.now_local(tz)
    upcoming_rows = appointments_in_range(business_id, now, now + timedelta(days=7))[:5]
    upcoming_appointments = [
        {"customer_name": row[1], "service": row[3], "start_time": row[4].astimezone(tz)}
        for row in upcoming_rows
    ]

    conversations = conversations_for_business(business_id)
    pending_count = sum(1 for c in conversations if c[6] == "pending")

    return render_template(
        "site_overview.html",
        business_name=session["business_name"],
        page=page,
        theme=theme,
        page_url_prefix=request.url_root + "p/",
        upcoming_appointments=upcoming_appointments,
        pending_count=pending_count,
        total_conversations=len(conversations),
        onboarding_done=request.args.get("onboarding_done"),
        subscription_active_flag=effective_subscription_active(business_id),
    )


# The conversation inbox - what used to live at "/" before Faza 15 moved the true landing
# page to home() above (see that route's own comment). Deliberately kept
# @login_required-only, same as /schedule and /settings - a visitor reaches those without
# passing back through onboarding_required/subscription_required's gate too, an existing
# tradeoff this split doesn't change, not a new gap.
@app.route("/conversations")
@login_required
def conversations_home():
    business_id = session["business_id"]
    conversations = conversations_for_business(business_id)
    archived = archived_conversations_for_business(business_id)
    trashed = trashed_conversations_for_business(business_id)

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
    today = replies_today_count(business_id)
    stats = business_stats(business_id)

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
        subscription_active_flag=effective_subscription_active(business_id),
        stats=stats,
    )


@app.route("/conversations/<int:conversation_id>/archive", methods=["POST"])
@login_required
def archive_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    archive_conversation(conversation_id)
    return redirect(url_for("conversations_home", filter=request.form.get("from_filter", "all")))


@app.route("/conversations/<int:conversation_id>/unarchive", methods=["POST"])
@login_required
def unarchive_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    unarchive_conversation(conversation_id)
    return redirect(url_for("conversations_home", filter=request.form.get("from_filter", "archived")))


@app.route("/conversations/<int:conversation_id>/delete", methods=["POST"])
@login_required
def delete_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    soft_delete_conversation(conversation_id)
    return redirect(url_for("conversations_home", filter=request.form.get("from_filter", "all")))


@app.route("/conversations/<int:conversation_id>/restore", methods=["POST"])
@login_required
def restore_conversation_route(conversation_id):
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation[1] != session["business_id"]:
        abort(404)
    restore_conversation(conversation_id)
    return redirect(url_for("conversations_home", filter=request.form.get("from_filter", "trash")))


def send_channel_reply(conversation, business, final_reply):
    """Shared by the web send_reply route below and api.py's api_send_reply. A page-chat
    conversation always reaches sent status the moment it's created -
    ai_replies.handle_incoming_message forces auto-send for "web" (there's no concept of
    a pending draft awaiting manual approval when the visitor was already shown the reply
    live) - so a manual /send here should never actually need to deliver anything; it's a
    no-op that exists so mark_sent() below still has something to call."""
    return None


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
        return redirect(url_for("conversations_home", send_error=1))

    mark_sent(conversation_id, final_reply)

    # Log the moment a business sends its very first reply - the key "aha" point in the funnel
    if business_stats(business_id)["total_sent"] == 1:
        log_event("first_reply_sent", business_id=business_id)

    # sent=1 -> dashboard shows a confirmation that the reply went out
    return redirect(url_for("conversations_home", sent=1))


# --- Schedule / Smart Booking (Faza 8) ---
#
# The calendar itself (view/manually add/cancel appointments) is available to every
# business regardless of plan - it's what the AI booking tool below writes into. Only
# the AI auto-booking a slot directly from a chat conversation is Pro-plan-exclusive;
# see has_pro_access() in database.py.

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
                    f"charge the card on file to keep your AI page and chat running "
                    f"without interruption.\n\n"
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


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
