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


def create_checkout_session(business_id, email, plan, interval, success_url, cancel_url):
    """Returns the Stripe-hosted Checkout URL to redirect the browser to, or None if
    Stripe isn't configured yet or the requested (plan, interval) has no price set."""
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
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=email,
        metadata=metadata,
        subscription_data={"metadata": metadata},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


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
