"""Stripe Checkout integration (Faza 10) - Starter/Pro x Monthly/Yearly subscriptions.

Mirrors the shape of the existing Paddle integration (see app.py's paddle_webhook /
templates/subscription.html's old Paddle.Checkout.open flow) but is simpler on the
frontend: Stripe Checkout is a plain server-redirect flow, so the pricing page just
POSTs to /subscription/checkout and gets redirected to session.url - no client-side
payment SDK/JS needed at all.

NOTE: Stripe does not support merchant accounts registered in Serbia as of this
writing, which is the whole reason this product used Paddle (a merchant-of-record) in
the first place - see the 2026-07 roadmap notes. This module is fully wired and ready
to go the moment a Stripe account exists for this business (or a supported-country
entity), but real checkout will not process a payment until STRIPE_SECRET_KEY and the
four STRIPE_PRICE_* env vars are set on Railway - until then, the pricing page shows
"Coming soon" on the Stripe buttons, same UX as an unconfigured Paddle price today.
"""
import os

import stripe

PRICE_ENV_VARS = {
    ("starter", "monthly"): "STRIPE_PRICE_STARTER_MONTHLY",
    ("starter", "yearly"): "STRIPE_PRICE_STARTER_YEARLY",
    ("pro", "monthly"): "STRIPE_PRICE_PRO_MONTHLY",
    ("pro", "yearly"): "STRIPE_PRICE_PRO_YEARLY",
}


def is_configured():
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def price_id_for(plan, interval):
    env_var = PRICE_ENV_VARS.get((plan, interval))
    return os.getenv(env_var, "") if env_var else ""


def create_checkout_session(business_id, email, plan, interval, success_url, cancel_url, trial_period_days=None):
    """Returns the Stripe-hosted Checkout URL to redirect the browser to, or None if
    Stripe isn't configured yet or the requested (plan, interval) has no price set.

    trial_period_days, when set (see app.py's stripe_checkout - only passed for a
    business's first-ever checkout), puts the new subscription on a real Stripe trial:
    the card is collected and validated now but not charged, Stripe auto-charges it the
    moment the trial ends unless the subscription was canceled before then, and Stripe
    fires customer.subscription.trial_will_end a few days before that happens so we can
    send our own heads-up email (see stripe_webhook) - the same "card on file, warn
    before the first real charge" pattern most subscription apps use."""
    if not is_configured():
        return None
    price_id = price_id_for(plan, interval)
    if not price_id:
        return None

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    # metadata travels on both the Checkout Session (read by checkout.session.completed)
    # AND the subscription itself via subscription_data.metadata (read by later
    # customer.subscription.updated/deleted events, which reference the subscription
    # object directly and never see the original Checkout Session again).
    metadata = {"business_id": str(business_id), "plan": plan}
    subscription_data = {"metadata": metadata}
    if trial_period_days:
        subscription_data["trial_period_days"] = trial_period_days
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=email,
        metadata=metadata,
        subscription_data=subscription_data,
        # Collect the card even though nothing is due today on a trial - required so
        # Stripe has something to charge automatically once the trial ends.
        payment_method_collection="always",
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def create_billing_portal_session(stripe_customer_id, return_url):
    """Returns a Stripe-hosted Customer Portal URL where a subscriber can update their
    payment method, view invoices, or cancel - self-service, so this app never needs to
    build any of that itself. None if Stripe isn't configured."""
    if not is_configured():
        return None
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.billing_portal.Session.create(customer=stripe_customer_id, return_url=return_url)
    return session.url


def get_subscription_status(stripe_subscription_id):
    """Fetches a subscription's current status directly from Stripe. Needed by
    checkout.session.completed: the Checkout Session payload itself never carries the
    resulting subscription's status, and it matters here specifically because a
    trial_period_days checkout starts life as 'trialing', not 'active'."""
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe.Subscription.retrieve(stripe_subscription_id).status


def verify_and_parse_webhook(payload, signature_header):
    """Verifies the Stripe-Signature header and returns the parsed event dict, or None
    if verification fails / the webhook secret isn't configured."""
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        return None
    try:
        return stripe.Webhook.construct_event(payload, signature_header, webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        return None
