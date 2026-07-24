"""Paddle Billing integration (back on Paddle, replacing the short-lived Stripe path -
see stripe_billing.py's own note: Stripe doesn't support a merchant account registered
in Serbia, and forming a company in some other country just to take payments isn't worth
it before this product has a single paying customer. Paddle is a merchant of record - it
sells on our behalf, so it doesn't care where WE are registered, only where the card-
holder is), Starter/Pro x Monthly/Yearly subscriptions with a real, card-backed 14-day
trial.

Unlike stripe_billing.py, there is no server-side "create a checkout session" call here.
Paddle Billing's checkout runs entirely client-side via Paddle.js (see
templates/subscription.html's Paddle.Initialize/Paddle.Checkout.open) - the server's only
job is telling the template which price IDs exist and which client token to hand Paddle.js,
then reacting to the webhook once Paddle reports what happened (see app.py's
paddle_webhook, unchanged). The trial period itself, and whether a card is required
during it, are configured on the Price object in the Paddle dashboard - not passed at
checkout time the way Stripe's trial_period_days is.
"""
import os

PRICE_ENV_VARS = {
    ("starter", "monthly"): "PADDLE_PRICE_STARTER_MONTHLY",
    ("starter", "yearly"): "PADDLE_PRICE_STARTER_YEARLY",
    ("pro", "monthly"): "PADDLE_PRICE_PRO_MONTHLY",
    ("pro", "yearly"): "PADDLE_PRICE_PRO_YEARLY",
}


def is_configured():
    return bool(os.getenv("PADDLE_CLIENT_TOKEN"))


def price_id_for(plan, interval):
    env_var = PRICE_ENV_VARS.get((plan, interval))
    return os.getenv(env_var, "") if env_var else ""


def client_token():
    """The PUBLIC client-side token Paddle.Initialize() needs in the browser - safe to
    render straight into the page, unlike PADDLE_API_KEY (server-only, not used by this
    module at all since checkout itself never calls Paddle's REST API from our server)."""
    return os.getenv("PADDLE_CLIENT_TOKEN", "")


def environment():
    """'sandbox' or 'production' - passed to Paddle.Environment.set() before Initialize
    so test purchases during setup don't hit real cards. Defaults to sandbox so a
    misconfigured deploy fails safe (test mode) rather than accidentally going live."""
    return os.getenv("PADDLE_ENV", "sandbox")
