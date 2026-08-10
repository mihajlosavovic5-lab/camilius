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
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

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


# A brand-new Google identity (no existing account matches it - see app.py's
# _google_login_existing) doesn't get an account created on the spot anymore - it's handed
# to the normal sign-up form as a name/email starting point, same as if the visitor had
# typed them in by hand, and the account is only actually created once they finish that
# form themselves (password, business details, terms). This short-lived signed
# token is how google_id survives that round trip without trusting a client-supplied value
# outright: register()/api_register() re-verify it, and only link the account to Google
# (skipping email confirmation - Google already proved it) if the token is still valid AND
# its embedded email matches whatever contact_email the form was actually submitted with.
LINK_TOKEN_MAX_AGE = 60 * 10  # 10 minutes - plenty to finish filling in the rest of the form


def _link_token_serializer():
    return URLSafeTimedSerializer(os.environ["FLASK_SECRET_KEY"], salt="google-link-token")


def make_link_token(google_id, email):
    return _link_token_serializer().dumps({"google_id": google_id, "email": email})


def verify_link_token(token):
    """Returns {"google_id": ..., "email": ...} if `token` is a still-valid, unexpired,
    unmodified token from make_link_token() above - None for anything else (missing,
    expired, tampered with), which callers treat as "no Google link, just a normal
    sign-up" rather than an error."""
    if not token:
        return None
    try:
        return _link_token_serializer().loads(token, max_age=LINK_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
