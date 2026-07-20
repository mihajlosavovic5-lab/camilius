import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_username,
    business_by_id, add_business, create_database, delete_business, replies_today_count,
    update_business, business_by_contact_email, save_reset_token, business_by_reset_token, set_new_password,
    link_subscription, update_subscription_status, get_subscription_status, subscription_active,
    business_stats, log_event, event_funnel, recent_client_errors, log_client_error,
    link_instagram_account, business_by_instagram_account_id, already_processed_messages,
    mark_message_processed, save_draft_reply, previous_conversations_with,
    log_webhook_hit, recent_webhook_hits,
)
from send_email import send_email
from generate_reply import generate_reply
from push_notifications import send_push_to_business
from instagram_api import (
    authorize_url as instagram_authorize_url, exchange_code_for_token, exchange_for_long_lived_token,
    get_account_info, subscribe_account_to_webhook, send_message as send_instagram_message,
    verify_webhook_challenge, verify_webhook_signature, parse_webhook_messages, get_sender_username,
    get_webhook_subscription_status,
)
from database import all_businesses
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

# During the free beta, all billing/subscription UI is hidden from users (nav link,
# dashboard banner, landing page pricing, the /subscription page itself) - nothing about
# payment is shown or mentioned anywhere. The underlying Paddle integration and the
# /subscription route are NOT removed, just unreachable/invisible, so flipping this back
# to False re-enables everything with no further code changes needed.
BETA_FREE_MODE = True


@app.context_processor
def inject_beta_free_mode():
    return {"beta_free_mode": BETA_FREE_MODE}


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


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        business = business_by_username(username)

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
            )
        except psycopg2.errors.UniqueViolation:
            return render_template("register.html", error="That username or contact email is already taken.", data=request.form)

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


def request_password_reset(email):
    """Shared by the web /forgot-password route and the mobile app's /api/forgot-password -
    looks up the account and, if found, emails a reset link. Always safe to call even if
    no account matches (just does nothing) - the caller is responsible for returning the
    same response either way, so as not to reveal whether an account exists."""
    business = business_by_contact_email(email)
    if not business:
        return

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    save_reset_token(business[0], token, expires)

    link = url_for("reset_password", token=token, _external=True)
    send_email(
        recipient=business[4],
        subject="Password reset - Camilius AI",
        body=(
            f"Hi,\n\n"
            f"Someone (hopefully you) requested a password reset for '{business[1]}'.\n\n"
            f"Your username is: {business[2]}\n\n"
            f"Click the link to set a new password (valid for 1 hour):\n{link}\n\n"
            f"If this wasn't you, feel free to ignore this email."
        ),
        business_name="Camilius AI",
        reply_to=os.getenv("ALERT_EMAIL") or business[4],
    )


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        request_password_reset(request.form["email"])
        # Same message regardless of whether the email was found - don't reveal whether an account exists
        return render_template(
            "forgot_password.html",
            message="If that email is registered, we've sent a password reset link.",
        )

    return render_template("forgot_password.html", message=None)


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
    business = business_by_id(session["business_id"])
    # indices: 0 id, 1 name, 4 contact_email, 5 instagram_account_id, 6 instagram_username,
    # 8 working_hours, 9 price_list, 10 faq
    contact_email = business[4]
    instagram_username = business[6]

    if request.method == "POST":
        name = request.form["name"]
        working_hours = request.form["working_hours"]
        price_list = request.form["price_list"]
        faq = request.form["faq"]

        update_business(session["business_id"], name, working_hours, price_list, faq)
        session["business_name"] = name

        return render_template(
            "settings.html",
            error=None,
            message="Your details have been saved.",
            business={
                "name": name, "contact_email": contact_email, "instagram_username": instagram_username,
                "working_hours": working_hours, "price_list": price_list, "faq": faq,
            },
        )

    return render_template(
        "settings.html",
        error=None,
        message=None,
        business={
            "name": business[1], "contact_email": contact_email, "instagram_username": instagram_username,
            "working_hours": business[8], "price_list": business[9], "faq": business[10],
        },
    )


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


# Proves to Android that the "Camilius AI" APK and this website are owned by the
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

    # Filter by status via ?filter=pending or ?filter=sent (default: all)
    filter_status = request.args.get("filter")
    if filter_status in ("pending", "sent"):
        shown = [c for c in conversations if c[6] == filter_status]
    else:
        filter_status = "all"
        shown = conversations

    pending_count = sum(1 for c in conversations if c[6] == "pending")
    today = replies_today_count(session["business_id"])
    current_subscription_status = get_subscription_status(session["business_id"])
    stats = business_stats(session["business_id"])

    return render_template(
        "dashboard.html",
        conversations=shown,
        business_name=session["business_name"],
        filter_status=filter_status,
        total_count=len(conversations),
        pending_count=pending_count,
        today=today,
        daily_limit=DAILY_REPLY_LIMIT,
        sent=request.args.get("sent"),
        send_error=request.args.get("send_error"),
        subscription_active_flag=subscription_active(current_subscription_status),
        stats=stats,
    )


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

    try:
        # Instagram only allows sending within 24h of the customer's last message (same
        # policy every DM platform has) - a send past that window fails here, which is an
        # expected business outcome (not a bug), hence the friendly redirect rather than
        # letting it 500.
        send_instagram_message(sender_igsid, final_reply, business[7], business[5])
    except requests.RequestException:
        return redirect(url_for("home", send_error=1))

    mark_sent(conversation_id, final_reply)

    # Log the moment a business sends its very first reply - the key "aha" point in the funnel
    if business_stats(business_id)["total_sent"] == 1:
        log_event("first_reply_sent", business_id=business_id)

    # sent=1 -> dashboard shows a confirmation that the reply went out
    return redirect(url_for("home", sent=1))


# --- Subscription (Paddle) ---

@app.route("/subscription")
@login_required
def subscription():
    if BETA_FREE_MODE:
        return redirect(url_for("home"))

    business = business_by_id(session["business_id"])
    status = get_subscription_status(session["business_id"])
    return render_template(
        "subscription.html",
        client_token=os.getenv("PADDLE_CLIENT_TOKEN", ""),
        price_id=os.getenv("PADDLE_PRICE_ID", ""),
        paddle_env=os.getenv("PADDLE_ENV", "sandbox"),
        business_id=session["business_id"],
        email=business[4],
        status=status,
        active=subscription_active(status),
    )


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
        # We sent our business_id through checkout as custom_data - that's how we link
        # the Paddle subscription to our business
        business_id = (data.get("custom_data") or {}).get("business_id")

        if business_id:
            link_subscription(int(business_id), customer_id, sub_id, status)
        elif sub_id:
            update_subscription_status(sub_id, status)

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
    # status (same reasoning as the dashboard banner elsewhere) - without this, a freshly
    # registered business defaults to subscription_status='none' and every message would
    # silently be dropped here until someone manually flips it to 'active'.
    if not BETA_FREE_MODE and not subscription_active(get_subscription_status(business_id)):
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
    print(f"[instagram-webhook] Generating AI reply for text={message['text']!r} from sender_igsid={sender_igsid}", flush=True)
    history = previous_conversations_with(business_id, sender_igsid)
    suggested_reply = generate_reply(message["text"], business_info, history=history)
    print(f"[instagram-webhook] AI reply generated: {suggested_reply!r}", flush=True)
    if suggested_reply.startswith("Sorry, I couldn't draft an automatic reply"):
        # generate_reply() already logged the finish_reason/refusal detail to Railway's
        # own logs (not reachable from here without the Railway CLI, which is blocked on
        # this machine) - mirror a note into client_errors too, since that IS reachable
        # via /admin/client-errors, so this doesn't go unnoticed.
        log_client_error(business_id, f"AI reply fallback used for text={message['text']!r}", "", "ai-reply-empty-debug", "")

    sender_display = get_sender_username(sender_igsid, access_token) or sender_igsid
    new_conversation_id = save_draft_reply(
        business_id, sender_display, message["text"], suggested_reply, sender_igsid=sender_igsid,
    )
    print(f"[instagram-webhook] Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    try:
        send_push_to_business(
            business_id,
            title="New customer question",
            body=f"{sender_display}: {message['text'][:80]}",
            data={"conversation_id": str(new_conversation_id)},
        )
    except Exception as error:
        # A push failure must never block draft processing
        print(f"[instagram-webhook] Push notification failed: {error}", flush=True)


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
