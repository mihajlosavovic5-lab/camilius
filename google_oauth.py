"""Continue with Google" sign-in - a plain OAuth 2.0 authorization-code flow implemented
by hand with `requests` (no authlib/google-auth dependency - this is the one OAuth round
trip the app needs, not worth pulling in a whole library for). Trusts Google's own
userinfo endpoint over TLS rather than verifying the id_token JWT's signature locally -
simpler, and just as trustworthy, since the call goes straight to Google, not through any
untrusted party.

Inert until GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are set (see is_configured(), same
opt-in pattern as paddle_billing.py/stripe_billing.py) - app.py only renders the "Continue
with Google" button and registers the /auth/google routes when this is configured, so an
unconfigured deploy behaves exactly as it did before this feature existed.
"""
import os
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def is_configured():
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def authorization_url(redirect_uri, state):
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Always shows Google's account chooser, even for a browser signed into exactly
        # one Google account - without this, a phone/system browser already logged into a
        # personal Google account silently reuses it, which surprises anyone who meant to
        # pick a different one (e.g. a work account) for their business.
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def fetch_user(code, redirect_uri):
    """Exchanges an authorization code for the signed-in Google account's profile
    (sub/email/email_verified/name - see https://openid.net/specs/openid-connect-core-1_0.html#UserInfoResponse).
    Returns None on any failure (bad/expired code, network blip, Google outage) - the
    caller treats that exactly like the visitor cancelling the consent screen, rather than
    raising and 500ing the callback route."""
    try:
        token_response = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        userinfo_response = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
        )
        userinfo_response.raise_for_status()
        return userinfo_response.json()
    except (requests.RequestException, KeyError, ValueError):
        return None
