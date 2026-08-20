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
import secrets
from urllib.parse import urlencode

import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import paddle_billing
from database import (
    business_by_google_id, business_by_contact_email, business_by_id, link_google_account,
    add_business, log_event, UniqueViolation,
)

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
    api.py's api_google_login_verify_id_token) hand that case to create_account() below
    instead, for a register-intent attempt - a login-intent attempt (explicitly "log me
    into an existing account") still just fails.

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


def create_account(google_user):
    """Creates a brand-new account directly from a Google identity login_existing() above
    found no match for, instead of handing it off to the sign-up form - "Continue with
    Google" now behaves the same way it does everywhere else: one tap either logs in or
    signs up, whichever applies, with nothing in between. Business details (working
    hours, price list, FAQ) are already optional at sign-up - fillable later from
    Settings/the mandatory "build your website" onboarding wizard, see app.py's
    onboarding_required - so nothing here needs typing in by hand before the account can
    exist at all: name/email come straight from Google, the password is a random value
    the visitor will never need (Google is how they'll sign back in - "Forgot password"
    still works if they ever want to set a real one), and email_verified=True since
    Google already proved the address.

    Returns the new business row. On the very rare race where someone else's sign-up (or
    a duplicate concurrent Google attempt) claims the same email between
    login_existing()'s lookup and this call, links this Google identity to whatever
    business_by_contact_email() finds instead - same outcome as login_existing's own
    "existing email" case - or None if even that can't find a row."""
    google_id = google_user.get("sub")
    email = (google_user.get("email") or "").strip().lower()
    name = (google_user.get("name") or "").strip() or (email.split("@")[0] if email else "")

    try:
        new_id = add_business(
            name=name,
            password=secrets.token_urlsafe(32),
            contact_email=email,
            working_hours="",
            price_list="",
            faq="",
            initial_status="none" if paddle_billing.is_configured() else "trialing",
            google_id=google_id,
            email_verified=True,
        )
    except UniqueViolation:
        existing = business_by_contact_email(email)
        if not existing:
            return None
        link_google_account(existing[0], google_id)
        return business_by_id(existing[0])

    log_event("register_completed", business_id=new_id)
    return business_by_id(new_id)


# Still used by register()/api_register() (see their own comments) as a fallback for
# whatever older mobile app installs still send a google_link_token - create_account()
# above is now the primary path for a brand-new Google identity, so nothing here mints a
# fresh one anymore, but a token minted before this account already exists must keep
# working exactly as before: it's how google_id survives the sign-up form's round trip
# without trusting a client-supplied value outright, re-verified server-side and only
# honored if the token is still valid AND its embedded email matches whatever
# contact_email the form was actually submitted with.
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
