"""Sends push notifications to the Android app via Firebase Cloud Messaging (FCM),
using a Firebase service account (no google-auth/firebase-admin dependency - just
cryptography + requests, which the project already depends on).

Inert until FIREBASE_PROJECT_ID and FIREBASE_SERVICE_ACCOUNT_JSON are set (same
gating pattern as ADMIN_TOKEN) - safe to import/call before Firebase is set up.
"""

import os
import json
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from database import push_tokens_for_business, remove_push_token, is_push_enabled

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URL = "https://oauth2.googleapis.com/token"

_cached_access_token = None
_cached_access_token_expires = 0


def _service_account():
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    return json.loads(raw)


def _base64url(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _get_access_token(account):
    """Signed-JWT OAuth2 flow for a Google service account - avoids needing the
    heavyweight google-auth library for what's otherwise a handful of lines."""
    global _cached_access_token, _cached_access_token_expires

    if _cached_access_token and time.time() < _cached_access_token_expires - 60:
        return _cached_access_token

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": account["client_email"],
        "scope": FCM_SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = _base64url(json.dumps(header).encode()) + b"." + _base64url(json.dumps(claims).encode())

    private_key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = (signing_input + b"." + _base64url(signature)).decode()

    response = requests.post(TOKEN_URL, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }, timeout=10)
    response.raise_for_status()
    data = response.json()

    _cached_access_token = data["access_token"]
    _cached_access_token_expires = now + data["expires_in"]
    return _cached_access_token


def send_push_to_business(business_id, title, body, data=None):
    """Sends a push notification to every device registered for this business.
    Silently does nothing if Firebase isn't configured yet, or if the business has
    turned notifications off in Settings - and prunes any device token FCM reports as
    no-longer-valid (app uninstalled, etc.)."""
    account = _service_account()
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not account or not project_id:
        return

    if not is_push_enabled(business_id):
        return

    tokens = push_tokens_for_business(business_id)
    if not tokens:
        return

    try:
        access_token = _get_access_token(account)
    except Exception as e:
        print(f"[push] Couldn't get an FCM access token: {e}")
        return

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    for token in tokens:
        message = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data or {},
                "android": {"priority": "high"},
            }
        }
        try:
            response = requests.post(url, headers=headers, json=message, timeout=10)
            if response.status_code == 404 or (
                response.status_code == 400 and "UNREGISTERED" in response.text
            ):
                remove_push_token(token)
            elif not response.ok:
                print(f"[push] FCM send failed ({response.status_code}): {response.text[:200]}")
        except Exception as e:
            print(f"[push] Error sending to a device: {e}")
