"""Tests for the card-backed Stripe trial (see stripe_billing.create_checkout_session's
trial_period_days, app.py's register()/stripe_checkout()/stripe_webhook, and database.py's
add_business(initial_status=...)/trial_expired() fix) - the "collect a card at trial
start, don't charge anything until day 14, warn a few days before the first real charge"
flow most subscription apps use."""
import app as app_module
import database
from conftest import get_csrf_token, register_business_web, web_post


def test_add_business_initial_status_none_grants_no_access(client, unique_username, unique_email):
    business_id = database.add_business(
        name="Test Salon", username=unique_username, password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    assert database.get_subscription_status(business_id) == "none"
    assert database.effective_subscription_active(business_id) is False


def test_trial_expired_ignores_self_serve_cutoff_once_a_stripe_subscription_is_linked(client, unique_username, unique_email):
    reg = register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]

    # Backdate created_at well past the 14-day self-serve cutoff - without a linked
    # processor subscription this alone is what trial_expired() keys off.
    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET created_at = created_at - INTERVAL '30 days' WHERE id = %s", (business_id,))
    connection.commit()
    connection.close()
    assert database.trial_expired(business_id) is True

    # Once a real Stripe subscription is linked (e.g. via checkout.session.completed),
    # Stripe's own subscription_status now owns expiry - the 14-day-from-created_at rule
    # must no longer apply, even though created_at is still backdated.
    database.link_stripe_subscription(business_id, "cust_1", "sub_1", "trialing", plan="pro")
    assert database.trial_expired(business_id) is False
    assert database.effective_subscription_active(business_id) is True


def test_subscription_required_does_not_claim_a_trial_ended_for_a_never_started_account(client, unique_username, unique_email):
    business_id = database.add_business(
        name="Test Salon", username=unique_username, password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    login_csrf = get_csrf_token(client, "/login")
    client.post("/login", data={"csrf_token": login_csrf, "username": unique_username, "password": "TestPassword123"})

    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your 14-day free trial has ended" not in body


def test_first_checkout_gets_a_trial_subsequent_ones_do_not(client, unique_username, unique_email, monkeypatch):
    # subscription_status='none' (register()'s post-Stripe-live path) is what puts the
    # business on the pricing/choose-a-plan page instead of the "trial already active"
    # card - that's the page whose Choose Starter/Pro forms POST to /subscription/checkout.
    business_id = database.add_business(
        name="Test Salon", username=unique_username, password="TestPassword123",
        contact_email=unique_email, working_hours="", price_list="", faq="",
        initial_status="none",
    )
    login_csrf = get_csrf_token(client, "/login")
    client.post("/login", data={"csrf_token": login_csrf, "username": unique_username, "password": "TestPassword123"})

    captured = {}

    def fake_create_checkout_session(*args, **kwargs):
        captured["trial_period_days"] = kwargs.get("trial_period_days")
        return "https://checkout.stripe.com/fake-session"

    monkeypatch.setattr(app_module.stripe_billing, "create_checkout_session", fake_create_checkout_session)

    csrf_token = get_csrf_token(client, "/subscription")
    web_post(client, "/subscription/checkout", {"csrf_token": csrf_token, "plan": "pro", "interval": "monthly"})
    assert captured["trial_period_days"] == 14

    # Simulate this business having already checked out through Stripe once before
    # (e.g. a past cancellation) - re-checking out must NOT grant another free trial.
    database.link_stripe_subscription(business_id, "cust_existing", "sub_existing", "canceled", plan="pro")
    csrf_token = get_csrf_token(client, "/subscription")
    web_post(client, "/subscription/checkout", {"csrf_token": csrf_token, "plan": "pro", "interval": "monthly"})
    assert captured["trial_period_days"] is None


def test_trial_will_end_webhook_emails_the_business(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    # subscription id is derived from unique_username (not a fixed literal) so repeated
    # local test runs against the same persistent dev Postgres never collide with a
    # leftover row from an earlier run - business_by_stripe_subscription_id has no way
    # to disambiguate two rows that happen to share a subscription id.
    subscription_id = f"sub_trial_{unique_username}"
    database.link_stripe_subscription(business_id, f"cust_{unique_username}", subscription_id, "trialing", plan="pro")

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
