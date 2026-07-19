"""Instagram DM integration - "Instagram API with Instagram Login" (the modern flow that
connects directly to a business's Instagram professional account, no linked Facebook
Page required). Replaces the old read_email.py (IMAP reading, now deleted) for reading
AND replaces send_email.py for replying to customers specifically - send_email.py itself
is kept, still used for account-recovery emails (password reset, etc.) via Resend.

Requires three env vars, set once the Meta Developer App exists (see the setup checklist
given separately - creating that app is a manual, browser-based step only the account
owner can do, same as every other third-party account this project has needed):
    INSTAGRAM_APP_ID
    INSTAGRAM_APP_SECRET
    INSTAGRAM_WEBHOOK_VERIFY_TOKEN - any string you make up, entered in the Meta App
        dashboard's webhook config field and compared against on the verification GET.

Endpoints/flow verified against Meta's current (2026) docs for this specific product
("Instagram API with Instagram Login"), not the older Facebook-Page-linked version:
- Authorize:      https://www.instagram.com/oauth/authorize
- Code exchange:  POST https://api.instagram.com/oauth/access_token  (short-lived, 1h)
- Long-lived:     GET  https://graph.instagram.com/access_token?grant_type=ig_exchange_token
- Refresh:        GET  https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token
- Send message:   POST https://graph.instagram.com/v21.0/{ig_id}/messages
Still worth re-checking against the live docs during first real testing - Meta revises
this API's details periodically and this was written from documentation, not a live test
account.
"""

import hashlib
import hmac
import os

import requests

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
CODE_EXCHANGE_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE = "https://graph.instagram.com"
API_VERSION = "v23.0"

# instagram_business_basic: read the account's own profile/id.
# instagram_business_manage_messages: read and send DMs. Both are required - this is the
# whole reason a business connects at all.
SCOPES = "instagram_business_basic,instagram_business_manage_messages"


def authorize_url(redirect_uri, state=None):
    app_id = os.environ["INSTAGRAM_APP_ID"]
    url = (
        f"{AUTHORIZE_URL}?client_id={app_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={SCOPES}"
    )
    if state:
        # Echoed back unmodified on the callback - the mobile app uses this to identify
        # which business is connecting instead of a website session cookie (it never has
        # one), see api.py's make_instagram_connect_state / app.py's instagram_oauth_callback.
        url += f"&state={state}"
    return url


def exchange_code_for_token(code, redirect_uri):
    """Step 1 of connecting an account: trades the one-time OAuth code (from the redirect
    after the business approves access) for a short-lived (1h) access token + their
    Instagram-scoped user id. Raises requests.HTTPError on failure (e.g. code already
    used, or expired - it's only valid once, for 1 hour)."""
    response = requests.post(CODE_EXCHANGE_URL, data={
        "client_id": os.environ["INSTAGRAM_APP_ID"],
        "client_secret": os.environ["INSTAGRAM_APP_SECRET"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    }, timeout=15)
    response.raise_for_status()
    return response.json()  # {"access_token": ..., "user_id": ..., "permissions": [...]}


def exchange_for_long_lived_token(short_lived_token):
    """Step 2: the short-lived token from above is only good for 1 hour - trade it for a
    long-lived one (60 days, refreshable) so the business doesn't have to reconnect
    constantly. Must happen server-side only (uses the app secret)."""
    response = requests.get(f"{GRAPH_BASE}/access_token", params={
        "grant_type": "ig_exchange_token",
        "client_secret": os.environ["INSTAGRAM_APP_SECRET"],
        "access_token": short_lived_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]  # also has expires_in, in seconds (~60 days)


def refresh_long_lived_token(access_token):
    """Long-lived tokens last 60 days but can be refreshed any time after the first 24h -
    call this periodically (the background worker does, well before the 60-day mark) so a
    connected business is never silently cut off by an expired token."""
    response = requests.get(f"{GRAPH_BASE}/refresh_access_token", params={
        "grant_type": "ig_refresh_token",
        "access_token": access_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def subscribe_account_to_webhook(access_token):
    """Without this call, connecting an account via OAuth alone does NOT make Instagram
    start sending webhook events for it - each account has to explicitly subscribe itself
    to the app's webhook after connecting. Call this once, right after link_instagram_account
    saves the token."""
    response = requests.post(f"{GRAPH_BASE}/{API_VERSION}/me/subscribed_apps", params={
        "subscribed_fields": "messages",
        "access_token": access_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


def get_webhook_subscription_status(access_token):
    """Diagnostic: returns what the account is currently subscribed to (GET on the same
    endpoint subscribe_account_to_webhook POSTs to). If the messages field isn't listed
    here, Instagram will NOT deliver message webhooks for this account, which is the most
    common reason a connected account's DMs never reach us."""
    response = requests.get(f"{GRAPH_BASE}/{API_VERSION}/me/subscribed_apps", params={
        "access_token": access_token,
    }, timeout=15)
    return {"http_status": response.status_code, "body": response.json() if response.content else None}


def get_account_info(access_token):
    """Returns {"user_id": ..., "username": ...} for the account that just connected -
    user_id is what Instagram's webhook payloads use to say "this message is for you"."""
    response = requests.get(f"{GRAPH_BASE}/me", params={
        "fields": "user_id,username",
        "access_token": access_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


def get_sender_username(sender_igsid, access_token):
    """Best-effort display name for a customer who messaged in - falls back to None (the
    caller then just displays the raw IGSID) if this fails; never worth blocking a draft
    reply over a missing display name."""
    try:
        response = requests.get(f"{GRAPH_BASE}/{API_VERSION}/{sender_igsid}", params={
            "fields": "name,username",
            "access_token": access_token,
        }, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("username") or data.get("name")
    except requests.RequestException:
        return None


def send_message(recipient_igsid, text, access_token, ig_account_id):
    """Sends the approved reply back to the customer's DM thread. Instagram only allows
    sending within a 24-hour window of the customer's last message (standard messaging
    platform policy, same as WhatsApp/Messenger) - a send outside that window fails with
    a policy error, which the caller should show to the user rather than silently drop."""
    response = requests.post(
        f"{GRAPH_BASE}/{API_VERSION}/{ig_account_id}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"recipient": {"id": recipient_igsid}, "message": {"text": text}},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def verify_webhook_challenge(mode, verify_token, challenge):
    """Meta's one-time GET verification when you first save the webhook URL in the App
    dashboard - must echo back hub.challenge as plain text if hub.verify_token matches
    what you configured (WEBHOOK_VERIFY_TOKEN, any string you made up), else reject."""
    if mode == "subscribe" and verify_token == os.environ["INSTAGRAM_WEBHOOK_VERIFY_TOKEN"]:
        return challenge
    return None


def verify_webhook_signature(raw_body, signature_header):
    """Every webhook POST is signed (X-Hub-Signature-256: sha256=<hex>) with the app
    secret - without checking this, anyone who finds the webhook URL could POST fake
    "new message" events. Compares with hmac.compare_digest to avoid timing attacks."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        os.environ["INSTAGRAM_APP_SECRET"].encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def parse_webhook_messages(payload):
    """Extracts (ig_account_id, sender_igsid, message_id, text) tuples from a webhook
    POST body's entry/messaging structure. Skips "echoes" (Instagram also delivers a
    webhook event for messages OUR OWN account sends - is_echo=true - which would
    otherwise get treated as a new incoming customer message and reprocessed forever)
    and non-text messages (images/reactions/etc - out of scope for now, an AI text
    reply doesn't apply to those anyway)."""
    messages = []
    for entry in payload.get("entry", []):
        ig_account_id = entry.get("id")
        for event in entry.get("messaging", []):
            message = event.get("message")
            if not message or message.get("is_echo") or "text" not in message:
                continue
            messages.append({
                "ig_account_id": ig_account_id,
                "sender_igsid": event.get("sender", {}).get("id"),
                "message_id": message.get("mid"),
                "text": message["text"],
            })
    return messages
