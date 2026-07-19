import os
from functools import wraps

from flask import Blueprint, request, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

from database import (
    conversations_for_business, get_conversation, mark_sent, business_by_username,
    business_by_id, replies_today_count, update_business, get_subscription_status,
    subscription_active, business_stats, log_event, save_push_token, log_client_error,
)
from send_email import send_email
from read_email import verify_credentials

api = Blueprint("api", __name__, url_prefix="/api")

# Current signed-APK release - bumped alongside android/app/build.gradle's versionName
# on every native release. app.py's /download route imports this too, so it's the one
# place to update.
APK_VERSION = "1.9.3"


@api.after_request
def add_cors_headers(response):
    # The mobile app's requests come from the bundled app shell's own origin, not this
    # site's - a wildcard is safe here because every route below is Bearer-token
    # authenticated (no ambient cookie), so a stray cross-site request without the
    # token still gets a 401 regardless of origin.
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
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    business = business_by_username(username)
    if not business or not check_password_hash(business[3], password):
        return jsonify(error="Wrong username or password."), 401

    return jsonify(token=make_token(business[0]), business_id=business[0], business_name=business[1])


@api.route("/dashboard")
@require_api_auth
def api_dashboard():
    from app import DAILY_REPLY_LIMIT, BETA_FREE_MODE  # deferred to avoid a circular import at module load

    business_id = request.business_id
    business = business_by_id(business_id)
    conversations = conversations_for_business(business_id)

    filter_status = request.args.get("filter")
    if filter_status in ("pending", "sent"):
        shown = [c for c in conversations if c[6] == filter_status]
    else:
        filter_status = "all"
        shown = conversations

    pending_count = sum(1 for c in conversations if c[6] == "pending")
    subscription_status = get_subscription_status(business_id)

    return jsonify(
        business_name=business[1],
        filter=filter_status,
        conversations=[_serialize_conversation(c) for c in shown],
        total_count=len(conversations),
        pending_count=pending_count,
        sent_count=len(conversations) - pending_count,
        today_processed=replies_today_count(business_id),
        daily_limit=DAILY_REPLY_LIMIT,
        subscription_active=subscription_active(subscription_status),
        beta_free_mode=BETA_FREE_MODE,
        stats=business_stats(business_id),
        # The app only ever calls PushNotifications.register() when this is true - avoids
        # touching that native/Firebase code path at all until a Firebase project actually
        # exists (FIREBASE_PROJECT_ID + FIREBASE_SERVICE_ACCOUNT_JSON are set on the server).
        push_enabled=bool(os.getenv("FIREBASE_PROJECT_ID")),
    )


@api.route("/conversations/<int:conversation_id>/send", methods=["POST"])
@require_api_auth
def api_send_reply(conversation_id):
    data = request.get_json(silent=True) or {}
    final_reply = data.get("reply", "")

    conversation = get_conversation(conversation_id)
    if conversation is None:
        return jsonify(error="not found"), 404

    _, business_id, sender, subject, question, suggested_reply = conversation
    if business_id != request.business_id:
        return jsonify(error="forbidden"), 403

    business = business_by_id(business_id)
    send_email(
        recipient=sender,
        subject=f"Re: {subject}",
        body=final_reply,
        business_name=business[1],
        reply_to=business[4],
    )
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
        email=business[4],
        working_hours=business[6],
        price_list=business[7],
        faq=business[8],
    )


@api.route("/settings", methods=["POST"])
@require_api_auth
def api_update_settings():
    data = request.get_json(silent=True) or {}
    business_id = request.business_id
    business = business_by_id(business_id)
    email = business[4]

    name = data.get("name", business[1])
    working_hours = data.get("working_hours", business[6])
    price_list = data.get("price_list", business[7])
    faq = data.get("faq", business[8])
    new_app_password = (data.get("email_app_password") or "").strip() or None

    if new_app_password and not verify_credentials(email, new_app_password):
        return jsonify(error="The new app password doesn't work - we couldn't connect to Gmail. Your previous data was kept."), 400

    update_business(business_id, name, working_hours, price_list, faq, email_app_password=new_app_password)
    return jsonify(message="Saved.", name=name, email=email, working_hours=working_hours, price_list=price_list, faq=faq)


@api.route("/push-token", methods=["POST"])
@require_api_auth
def api_save_push_token():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="missing token"), 400
    save_push_token(request.business_id, token)
    return jsonify(message="saved")


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
# APK build for every small change. MOBILE_BUNDLE_VERSION/URL are set once the bundle
# is actually built and uploaded (see build_mobile_bundle.py).
@api.route("/mobile-manifest")
def api_mobile_manifest():
    version = os.getenv("MOBILE_BUNDLE_VERSION")
    if not version:
        return jsonify(available=False)
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
