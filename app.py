import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_username,
    business_by_id, add_business, create_database, delete_business, replies_today_count,
    update_business, business_by_email, save_reset_token, business_by_reset_token, set_new_password,
    link_subscription, update_subscription_status, get_subscription_status, subscription_active,
    business_stats, log_event, event_funnel,
)
from send_email import send_email
from read_email import verify_credentials
from api import api as api_blueprint

load_dotenv()

app = Flask(__name__)
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

limiter = Limiter(get_remote_address, app=app, default_limits=[])
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

        # Verify the entered Gmail + app password actually work before creating the account.
        # This proves the user controls the account and that the system will be able to read messages.
        if not verify_credentials(request.form["email"], request.form["email_app_password"]):
            return render_template(
                "register.html",
                error="We couldn't connect to Gmail with that address and app password. Check that the app password is correct (16 characters from your Google account) and that IMAP is enabled in Gmail settings.",
                data=request.form,
            )

        try:
            new_id = add_business(
                name=request.form["name"],
                username=request.form["username"],
                password=request.form["password"],
                email=request.form["email"],
                email_app_password=request.form["email_app_password"],
                working_hours=request.form["working_hours"],
                price_list=request.form["price_list"],
                faq=request.form["faq"],
            )
        except psycopg2.errors.UniqueViolation:
            return render_template("register.html", error="That username or email address is already taken.", data=request.form)

        log_event("register_completed", business_id=new_id)
        session["business_id"] = new_id
        session["business_name"] = request.form["name"]
        return redirect(url_for("home"))

    if not session.get("_seen_register"):
        log_event("register_view")
        session["_seen_register"] = True
    return render_template("register.html", error=None, data={})


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        business = business_by_email(request.form["email"])

        if business:
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            save_reset_token(business[0], token, expires)

            link = url_for("reset_password", token=token, _external=True)
            send_email(
                recipient=business[4],
                subject="Password reset - Camilius AI",
                body=(
                    f"Hi,\n\n"
                    f"Someone (hopefully you) requested a password reset for the account '{business[1]}'.\n\n"
                    f"Click the link to set a new password (valid for 1 hour):\n{link}\n\n"
                    f"If this wasn't you, feel free to ignore this email."
                ),
                business_name="Camilius AI",
                reply_to=os.getenv("ALERT_EMAIL") or business[4],
            )

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
    # indices: 0 id, 1 name, 4 email, 6 working_hours, 7 price_list, 8 faq
    email = business[4]

    if request.method == "POST":
        name = request.form["name"]
        working_hours = request.form["working_hours"]
        price_list = request.form["price_list"]
        faq = request.form["faq"]
        # Empty field = keep the existing app password; filled in = change it
        new_app_password = request.form.get("email_app_password", "").strip() or None

        # If changing the app password, first verify the new one actually works (same as at registration)
        if new_app_password and not verify_credentials(email, new_app_password):
            return render_template(
                "settings.html",
                error="The new app password doesn't work - we couldn't connect to Gmail. Your previous data was kept.",
                business={"name": name, "email": email, "working_hours": working_hours, "price_list": price_list, "faq": faq},
                message=None,
            )

        update_business(session["business_id"], name, working_hours, price_list, faq, email_app_password=new_app_password)
        session["business_name"] = name

        return render_template(
            "settings.html",
            error=None,
            message="Your details have been saved.",
            business={"name": name, "email": email, "working_hours": working_hours, "price_list": price_list, "faq": faq},
        )

    return render_template(
        "settings.html",
        error=None,
        message=None,
        business={"name": business[1], "email": email, "working_hours": business[6], "price_list": business[7], "faq": business[8]},
    )


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    delete_business(session["business_id"])
    session.clear()
    return redirect(url_for("login"))


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


@app.route("/gmail-help")
def gmail_help():
    return render_template("gmail_help.html")


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
APK_VERSION = "1.8.0"


@app.route("/download/camilius-ai.apk")
def download_apk():
    return send_from_directory(
        app.static_folder,
        "camilius-ai.apk",
        as_attachment=True,
        download_name=f"camilius-ai-v{APK_VERSION}.apk",
    )


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


DAILY_REPLY_LIMIT = 50  # same limit as in process_emails.py


@app.route("/")
def home():
    # Logged-out visitors see the public landing page (marketing + call to register)
    # instead of a redirect to login. Logged-in visitors get the dashboard.
    if not session.get("business_id") or business_by_id(session["business_id"]) is None:
        # Read the flag before clear() wipes it - it's what lets us count each
        # visitor session once instead of logging every page refresh.
        already_seen = session.get("_seen_landing", False)
        session.clear()
        session["_seen_landing"] = True
        if not already_seen:
            log_event("landing_view")
        return render_template("landing.html")

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
        subscription_active_flag=subscription_active(current_subscription_status),
        stats=stats,
    )


@app.route("/send/<int:conversation_id>", methods=["POST"])
@login_required
def send_reply(conversation_id):
    final_reply = request.form["reply"]

    _, business_id, sender, subject, question, suggested_reply = get_conversation(conversation_id)

    # Security check: this conversation must belong to the logged-in business,
    # otherwise someone could guess other people's IDs in the URL and send replies on their behalf
    if business_id != session["business_id"]:
        abort(403)

    business = business_by_id(business_id)

    send_email(
        recipient=sender,
        subject=f"Re: {subject}",
        body=final_reply,
        business_name=business[1],
        reply_to=business[4],
    )

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


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
