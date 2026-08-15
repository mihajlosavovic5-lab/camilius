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

from database import business_by_google_id, business_by_contact_email, business_by_id, link_google_account

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


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


def verify_id_token(id_token):
    """Verifies an ID token minted natively on-device (Android Credential Manager, see
    api.py's api_google_login_verify_id_token) - the counterpart to fetch_user() for the
    code-exchange flow, same "trust Google's own endpoint over TLS" approach. Google's
    tokeninfo endpoint validates the JWT's signature and expiry itself; the `aud` check
    below is still ours to make, since tokeninfo validates a token minted for some *other*
    app just as well - skipping it would let any app's Google ID token sign into this one.
    Returns None on any failure, same convention as fetch_user()."""
    try:
        response = requests.get(TOKENINFO_URL, params={"id_token": id_token}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if payload.get("aud") != os.environ["GOOGLE_CLIENT_ID"]:
            return None
        # tokeninfo returns this as the string "true"/"false" (unlike userinfo's real
        # boolean) - normalized here so callers can treat both fetch_user() and this the
        # same way.
        payload["email_verified"] = payload.get("email_verified") == "true"
        return payload
    except (requests.RequestException, KeyError, ValueError):
        return None


def login_existing(google_user):
    """Looks up (and links, on the first match) the existing business this Google identity
    belongs to - never creates one. Returns the business row, or None if this Google
    account has never been seen here before. Callers (app.py's auth_google_callback,
    api.py's api_google_login_verify_id_token) send that case to the ordinary sign-up form
    instead, pre-filled with whatever Google handed over - "Continue with Google" is a
    shortcut INTO the normal flow for a first-time visitor, not a way to skip filling in the
    rest of the account (password, business details) that a manual sign-up would ask for
    anyway.

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


# A brand-new Google identity (no existing account matches it - see login_existing above)
# doesn't get an account created on the spot anymore - it's handed
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
