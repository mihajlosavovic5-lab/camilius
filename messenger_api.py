"""Facebook Messenger DM integration - a third message channel alongside Instagram
(instagram_api.py) and WhatsApp (whatsapp_api.py). Unlike WhatsApp, this DOES use a real
OAuth redirect - Facebook Login (not Instagram Login), since Messenger messages are
sent/received through a Facebook Page, not a personal account, and a Page Access Token
obtained from a long-lived user token is effectively non-expiring (Meta's own docs/dev
forum: invalidated only by the business revoking app permission, a security checkup, a
Page role change, or a manual reset in Business Settings - no refresh loop needed the way
Instagram's 60-day token has one).

Same App Review/Business Verification wall as Instagram still applies before the general
public can connect - see app.py's INSTAGRAM_TESTERS_ONLY comment. Critically, Messenger's
Dev Mode gating is a SEPARATE list from Instagram Testers: it's the app's general App
Dashboard > App roles > Roles (Admin/Developer/Tester), not the Instagram-specific
"Instagram Testers" sub-list - an existing Instagram Tester does NOT automatically also
work here. The business's Facebook account has to be separately added there, and they
accept via developers.facebook.com under their own Facebook account (not the Instagram
app's Tester Invites flow).

A business can manage more than one Facebook Page, so the connect flow has an extra step
Instagram's doesn't: after OAuth, GET /me/accounts lists every Page the person manages -
if there's exactly one, auto-connect it (the common case for a small business); if more
than one, app.py shows a picker.

Requires three env vars, set once the Meta App has the Messenger product added (can be
the same Meta App already used for Instagram, or a different one - if the same App ID/
Secret, these three can just be set to the same values as their Instagram counterparts):
    MESSENGER_APP_ID
    MESSENGER_APP_SECRET
    MESSENGER_WEBHOOK_VERIFY_TOKEN - any string you make up, entered in the Meta App
        dashboard's webhook config field and compared against on the verification GET.

Endpoints verified against Meta's current (2026) Messenger Platform docs, written from
documentation rather than a live test account (Camilius has no Facebook Page connected
yet to test against) - worth re-checking against the live docs during first real setup,
same caveat instagram_api.py's own docstring already carries for that module.
- Authorize:              https://www.facebook.com/v23.0/dialog/oauth
- Code exchange:          GET  https://graph.facebook.com/v23.0/oauth/access_token
- Long-lived token:       GET  https://graph.facebook.com/v23.0/oauth/access_token (grant_type=fb_exchange_token)
- List managed Pages:     GET  https://graph.facebook.com/v23.0/me/accounts
- Subscribe to webhook:   POST https://graph.facebook.com/v23.0/{page-id}/subscribed_apps
- Send message:           POST https://graph.facebook.com/v23.0/me/messages
"""

import hashlib
import hmac
import os

import requests

AUTHORIZE_URL = "https://www.facebook.com/v23.0/dialog/oauth"
GRAPH_BASE = "https://graph.facebook.com"
API_VERSION = "v23.0"

# pages_show_list: list which Pages this person manages (the picker step below).
# pages_messaging: read and send DMs on a Page - the whole reason a business connects.
# pages_manage_metadata: required to subscribe a Page to Camilius's webhook at all.
SCOPES = "pages_show_list,pages_messaging,pages_manage_metadata"


def authorize_url(redirect_uri, state=None):
    app_id = os.environ["MESSENGER_APP_ID"]
    url = (
        f"{AUTHORIZE_URL}?client_id={app_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={SCOPES}"
    )
    if state:
        url += f"&state={state}"
    return url


def exchange_code_for_token(code, redirect_uri):
    """Step 1: trades the one-time OAuth code for a short-lived user access token.
    Mirrors instagram_api.exchange_code_for_token."""
    response = requests.get(f"{GRAPH_BASE}/{API_VERSION}/oauth/access_token", params={
        "client_id": os.environ["MESSENGER_APP_ID"],
        "client_secret": os.environ["MESSENGER_APP_SECRET"],
        "redirect_uri": redirect_uri,
        "code": code,
    }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def exchange_for_long_lived_token(short_lived_token):
    """Step 2: trades the short-lived user token for a long-lived (60-day) one - needed
    before the Page picker below, since a Page Access Token derived from a short-lived
    user token would itself only be short-lived."""
    response = requests.get(f"{GRAPH_BASE}/{API_VERSION}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": os.environ["MESSENGER_APP_ID"],
        "client_secret": os.environ["MESSENGER_APP_SECRET"],
        "fb_exchange_token": short_lived_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def get_pages(long_lived_user_token):
    """Step 3: lists every Facebook Page this person manages, each with its own Page
    Access Token - the "which Page" picker in app.py's messenger_oauth_callback. Returns
    [{"id": ..., "name": ..., "access_token": ...}, ...]."""
    response = requests.get(f"{GRAPH_BASE}/{API_VERSION}/me/accounts", params={
        "access_token": long_lived_user_token,
    }, timeout=15)
    response.raise_for_status()
    return response.json().get("data", [])


def subscribe_page_to_webhook(page_id, page_access_token):
    """Without this call, a connected Page does NOT receive webhook events - mirrors
    instagram_api.subscribe_account_to_webhook. Call this once, right after
    link_messenger_page saves the chosen Page's token."""
    response = requests.post(
        f"{GRAPH_BASE}/{API_VERSION}/{page_id}/subscribed_apps",
        params={"subscribed_fields": "messages", "access_token": page_access_token},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def send_message(recipient_psid, text, page_access_token):
    """Sends the approved reply back to the customer's Messenger thread. Like Instagram/
    WhatsApp, only allowed within a 24-hour window of the customer's last message - a send
    outside that window fails with a policy error, which the caller should show to the
    user rather than silently drop."""
    response = requests.post(
        f"{GRAPH_BASE}/{API_VERSION}/me/messages",
        params={"access_token": page_access_token},
        json={
            "recipient": {"id": recipient_psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def verify_webhook_challenge(mode, verify_token, challenge):
    """Meta's one-time GET verification when first saving the webhook URL in the App
    dashboard. Mirrors instagram_api.verify_webhook_challenge."""
    if mode == "subscribe" and verify_token == os.environ["MESSENGER_WEBHOOK_VERIFY_TOKEN"]:
        return challenge
    return None


def verify_webhook_signature(raw_body, signature_header):
    """Every webhook POST is signed (X-Hub-Signature-256: sha256=<hex>) with the app
    secret. Mirrors instagram_api.verify_webhook_signature."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        os.environ["MESSENGER_APP_SECRET"].encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def parse_webhook_messages(payload):
    """Extracts (page_id, sender_psid, message_id, text) tuples from a webhook POST
    body's entry/messaging structure (object="page", distinct from Instagram's
    "instagram" and WhatsApp's "whatsapp_business_account"). Skips echoes (is_echo=true -
    Messenger also delivers a webhook event for messages the Page itself sends, same as
    Instagram) and non-text messages, same reasoning as instagram_api.parse_webhook_messages."""
    messages = []
    for entry in payload.get("entry", []):
        page_id = entry.get("id")
        for event in entry.get("messaging", []):
            message = event.get("message")
            if not message or message.get("is_echo") or "text" not in message:
                continue
            messages.append({
                "page_id": page_id,
                "sender_psid": event.get("sender", {}).get("id"),
                "message_id": message.get("mid"),
                "text": message["text"],
            })
    return messages
