"""WhatsApp DM integration - a second message channel alongside Instagram (instagram_api.py),
using the WhatsApp Cloud API directly (Meta's own hosted API, not a third-party BSP).

Unlike Instagram, there's no per-business OAuth flow here. To onboard OTHER businesses'
own WhatsApp numbers via Meta's Embedded Signup, Camilius would need to be an approved
Meta "Tech Provider" - which itself requires Meta Business Verification, the exact same
wall currently blocking Instagram App Review (see app.py's INSTAGRAM_TESTERS_ONLY comment).
So instead: each business generates its own permanent access token directly in its own
Meta Business Suite (System User token, scoped to Camilius's Meta App as a partner) and
pastes it into Settings (see app.py's connect_whatsapp route) - Camilius never requests
access to a business's account through its own app, the business grants it directly.
This works today regardless of Camilius's own verification status, at the cost of a more
manual (paste, not one-click) connect step.

The webhook itself is still configured once, centrally, on Camilius's own Meta App (App
dashboard > WhatsApp > Configuration > Webhook) - each connected business's WhatsApp
Business Account (WABA) is subscribed to it individually via subscribe_account_to_webhook
below, using the token that business pasted in.

Requires two env vars, set once on the Meta App that owns the webhook:
    WHATSAPP_APP_SECRET - that app's secret, used to verify incoming webhook signatures.
    WHATSAPP_WEBHOOK_VERIFY_TOKEN - any string you make up, entered in the Meta App
        dashboard's webhook config field and compared against on the verification GET.
(No app-id/app-secret pair is needed for token exchange the way Instagram needs one -
there's no code-for-token exchange here at all, see above.)

Endpoints verified against Meta's current (2026) WhatsApp Cloud API docs, written from
documentation rather than a live test account (Camilius has no real WhatsApp Business
Account to test against yet) - worth re-checking against the live docs during first real
setup, same caveat instagram_api.py's own docstring already carries for that module.
- Send message:        POST https://graph.facebook.com/v23.0/{phone_number_id}/messages
- Subscribe to webhook: POST https://graph.facebook.com/v23.0/{waba_id}/subscribed_apps
"""

import hashlib
import hmac
import os

import requests

GRAPH_BASE = "https://graph.facebook.com"
API_VERSION = "v23.0"


def send_message(recipient_wa_id, text, access_token, phone_number_id):
    """Sends the approved reply back to the customer's WhatsApp chat. Like Instagram,
    free-form replies are only allowed within a 24-hour window of the customer's last
    message (WhatsApp's "customer service window") - a send outside that window fails
    with a policy error, which the caller should show to the user rather than silently
    drop. (Outside that window, only pre-approved message templates can be sent - not
    something Camilius's AI-drafted replies are, so a stale conversation just can't be
    replied to here, same as Instagram's own 24h limit.)"""
    response = requests.post(
        f"{GRAPH_BASE}/{API_VERSION}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": recipient_wa_id,
            "type": "text",
            "text": {"body": text},
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def subscribe_account_to_webhook(waba_id, access_token):
    """Without this call, a business pasting in a valid access token alone does NOT make
    WhatsApp start sending webhook events for its number - the WhatsApp Business Account
    (WABA, not the phone number itself) has to explicitly subscribe to Camilius's app's
    webhook. Call this once, right after link_whatsapp_account saves the pasted token -
    mirrors instagram_api.subscribe_account_to_webhook."""
    response = requests.post(
        f"{GRAPH_BASE}/{API_VERSION}/{waba_id}/subscribed_apps",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def verify_webhook_challenge(mode, verify_token, challenge):
    """Meta's one-time GET verification when first saving the webhook URL in the App
    dashboard - must echo back hub.challenge as plain text if hub.verify_token matches
    what you configured (WHATSAPP_WEBHOOK_VERIFY_TOKEN, any string you made up), else
    reject. Mirrors instagram_api.verify_webhook_challenge."""
    if mode == "subscribe" and verify_token == os.environ["WHATSAPP_WEBHOOK_VERIFY_TOKEN"]:
        return challenge
    return None


def verify_webhook_signature(raw_body, signature_header):
    """Every webhook POST is signed (X-Hub-Signature-256: sha256=<hex>) with the app
    secret of whichever Meta App owns the webhook - without checking this, anyone who
    found the webhook URL could POST fake "new message" events for any connected
    business. Compares with hmac.compare_digest to avoid timing attacks. Mirrors
    instagram_api.verify_webhook_signature."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        os.environ["WHATSAPP_APP_SECRET"].encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def parse_webhook_messages(payload):
    """Extracts (phone_number_id, sender_wa_id, message_id, sender_name, text) tuples
    from a webhook POST body's entry/changes structure. Skips delivery/read-receipt
    events (value.statuses, not value.messages - WhatsApp delivers those on the same
    webhook) and non-text messages (images/reactions/etc - out of scope, same as
    Instagram's parser skipping those)."""
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            contacts = {c.get("wa_id"): c for c in value.get("contacts", [])}
            for message in value.get("messages", []):
                if message.get("type") != "text" or "text" not in message:
                    continue
                sender_wa_id = message.get("from")
                sender_name = contacts.get(sender_wa_id, {}).get("profile", {}).get("name")
                messages.append({
                    "phone_number_id": phone_number_id,
                    "sender_wa_id": sender_wa_id,
                    "message_id": message.get("id"),
                    "sender_name": sender_name,
                    "text": message["text"]["body"],
                })
    return messages
