"""Tests for the card-backed Paddle trial (back on Paddle - see paddle_billing.py's
module docstring for why Stripe didn't work out) - the "collect a card at trial start,
don't charge anything until day 14, warn a few days before the first real charge" flow
most subscription apps use. Covers: database.py's add_business(initial_status=...)/
trial_expired()/businesses_needing_trial_warning(), app.py's register()/paddle_webhook,
and reminders.py's send_trial_ending_warnings_if_needed. Also keeps a couple of tests for
the still-present-but-dormant Stripe path (stripe_webhook, stripe_subscription linking),
since that code stays in the repo for if a Stripe-eligible entity exists later."""
import hashlib
import hmac
import time
from datetime import datetime, timedelta

import api as api_module
import app as app_module
import database
from conftest import get_csrf_token, register_business, register_business_web


def test_add_business_initial_status_none_grants_no_access(client, unique_email):
    business_id = database.add_business(
        name="Test Salon", password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    assert database.get_subscription_status(business_id) == "none"
    assert database.effective_subscription_active(business_id) is False


def test_trial_expired_ignores_self_serve_cutoff_once_a_paddle_subscription_is_linked(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]

    # Backdate created_at well past the 14-day self-serve cutoff - without a linked
    # processor subscription this alone is what trial_expired() keys off.
    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET created_at = created_at - INTERVAL '30 days' WHERE id = %s", (business_id,))
    connection.commit()
    connection.close()
    assert database.trial_expired(business_id) is True

    # Once a real Paddle subscription is linked (e.g. via paddle_webhook), Paddle's own
    # subscription_status now owns expiry - the 14-day-from-created_at rule must no
    # longer apply, even though created_at is still backdated.
    database.link_subscription(business_id, "cust_1", "sub_1", "trialing", plan="pro")
    assert database.trial_expired(business_id) is False
    assert database.effective_subscription_active(business_id) is True


def test_subscription_required_does_not_claim_a_trial_ended_for_a_never_started_account(client, unique_email):
    business_id = database.add_business(
        name="Test Salon", password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    login_csrf = get_csrf_token(client, "/login")
    client.post("/login", data={"csrf_token": login_csrf, "email": unique_email, "password": "TestPassword123"})

    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your 14-day free trial has ended" not in body


def test_pricing_page_shows_paddle_checkout_once_configured(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    monkeypatch.setattr(app_module.paddle_billing, "is_configured", lambda: True)
    monkeypatch.setattr(app_module.paddle_billing, "client_token", lambda: "test_client_token")
    monkeypatch.setattr(app_module.paddle_billing, "environment", lambda: "sandbox")
    monkeypatch.setattr(app_module.paddle_billing, "price_id_for", lambda plan, interval: f"pri_{plan}_{interval}")

    business_id = database.add_business(
        name="Test Salon", password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    database.mark_email_verified(business_id)  # login() redirects to /verify-pending otherwise
    login_csrf = get_csrf_token(client, "/login")
    client.post("/login", data={"csrf_token": login_csrf, "email": unique_email, "password": "TestPassword123"})

    response = client.get("/subscription")
    body = response.get_data(as_text=True)
    assert "cdn.paddle.com" in body
    assert "pri_pro_monthly" in body
    assert "Coming soon" not in body


def test_pricing_page_shows_coming_soon_when_paddle_not_configured(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    business_id = database.add_business(
        name="Test Salon", password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    database.mark_email_verified(business_id)  # login() redirects to /verify-pending otherwise
    login_csrf = get_csrf_token(client, "/login")
    client.post("/login", data={"csrf_token": login_csrf, "email": unique_email, "password": "TestPassword123"})

    response = client.get("/subscription")
    body = response.get_data(as_text=True)
    assert "Coming soon" in body
    assert "cdn.paddle.com" not in body


def _sign_paddle_payload(raw_body, secret):
    ts = str(int(time.time()))
    signed = ts.encode() + b":" + raw_body
    h1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def test_paddle_webhook_links_subscription_and_captures_trial_end(client, unique_email, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "test-paddle-webhook-secret")
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]

    next_billed_at = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = (
        b'{"event_type": "subscription.created", "data": {'
        b'"id": "sub_new_1", "status": "trialing", "customer_id": "cust_new_1", '
        b'"next_billed_at": "' + next_billed_at.encode() + b'", '
        b'"custom_data": {"business_id": "' + str(business_id).encode() + b'", "plan": "pro"}'
        b"}}"
    )
    signature = _sign_paddle_payload(payload, "test-paddle-webhook-secret")

    response = client.post("/paddle-webhook", data=payload, content_type="application/json", headers={"Paddle-Signature": signature})
    assert response.status_code == 200
    assert database.get_subscription_status(business_id) == "trialing"
    assert database.get_subscription_plan(business_id) == "pro"

    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("SELECT paddle_trial_ends_at FROM businesses WHERE id = %s", (business_id,))
    trial_ends_at = cursor.fetchone()[0]
    connection.close()
    assert trial_ends_at is not None
    assert abs((trial_ends_at - (datetime.utcnow() + timedelta(days=14))).total_seconds()) < 60


def test_businesses_needing_trial_warning_and_dedup(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]

    # Trial ending in 1 day is within the 3-day warning window.
    soon = datetime.utcnow() + timedelta(days=1)
    database.link_subscription(business_id, "cust_soon", "sub_soon", "trialing", plan="pro", trial_ends_at=soon)

    matches = database.businesses_needing_trial_warning()
    assert any(row[0] == business_id for row in matches)

    database.mark_trial_warning_sent(business_id)
    matches_after = database.businesses_needing_trial_warning()
    assert not any(row[0] == business_id for row in matches_after)


def test_businesses_needing_trial_warning_excludes_far_off_trials(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]

    far_off = datetime.utcnow() + timedelta(days=10)
    database.link_subscription(business_id, "cust_far", "sub_far", "trialing", plan="pro", trial_ends_at=far_off)

    matches = database.businesses_needing_trial_warning()
    assert not any(row[0] == business_id for row in matches)


def test_send_trial_ending_warnings_if_needed_sends_and_dedupes(client, unique_email, monkeypatch):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    soon = datetime.utcnow() + timedelta(days=2)
    database.link_subscription(business_id, "cust_warn", "sub_warn", "trialing", plan="pro", trial_ends_at=soon)

    import reminders
    sent = []
    monkeypatch.setattr(reminders, "send_email", lambda **kwargs: sent.append(kwargs))

    reminders.send_trial_ending_warnings_if_needed()
    assert len(sent) == 1
    assert sent[0]["recipient"] == unique_email
    assert "trial" in sent[0]["subject"].lower()

    # Second run must not send again - trial_warning_sent_at now dedupes it.
    reminders.send_trial_ending_warnings_if_needed()
    assert len(sent) == 1


def test_mobile_register_gets_card_free_trial_when_paddle_not_configured(client, unique_email):
    # Default test env has no PADDLE_CLIENT_TOKEN - api_register() must behave exactly
    # like before (unaffected businesses shouldn't need a card at all).
    reg = register_business(client, unique_email)
    business_id = reg.get_json()["business_id"]
    assert database.get_subscription_status(business_id) == "trialing"


def test_mobile_register_requires_a_card_once_paddle_is_configured(client, unique_email, monkeypatch):
    # Mirrors register()'s web behavior (see app.py) - api_register() must gate on the
    # same paddle_billing.is_configured() check, not silently keep the old free trial.
    monkeypatch.setattr(api_module.paddle_billing, "is_configured", lambda: True)
    reg = register_business(client, unique_email)
    business_id = reg.get_json()["business_id"]
    assert database.get_subscription_status(business_id) == "none"
    assert database.effective_subscription_active(business_id) is False


# --- Stripe path stays wired in the backend (dormant, unlinked from the pricing UI) -
# keeping a couple of tests so a future refactor doesn't silently break it. ---

def test_stripe_subscription_link_still_works_as_a_dormant_path(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    database.link_stripe_subscription(business_id, "stripe_cust_1", "stripe_sub_1", "trialing", plan="pro")
    assert database.get_subscription_status(business_id) == "trialing"
    assert database.trial_expired(business_id) is False


def test_stripe_trial_will_end_webhook_still_emails_the_business(client, unique_email, monkeypatch):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    subscription_id = f"sub_trial_{business_id}"
    database.link_stripe_subscription(business_id, f"cust_{business_id}", subscription_id, "trialing", plan="pro")

    sent = {}
    monkeypatch.setattr(app_module, "send_email", lambda **kwargs: sent.update(kwargs))
    monkeypatch.setattr(
        app_module.stripe_billing, "verify_and_parse_webhook",
        lambda payload, sig: {
            "type": "customer.subscription.trial_will_end",
            "data": {"object": {"id": subscription_id, "trial_end": 1234567890}},
        },
    )

    response = client.post("/stripe-webhook", data=b"{}", headers={"Stripe-Signature": "test"})
    assert response.status_code == 200
    assert sent["recipient"] == unique_email
    assert "trial" in sent["subject"].lower()
