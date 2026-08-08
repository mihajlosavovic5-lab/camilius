"""Buys a real, independently-registrable domain (mihajlo-prodaja.com) the owner chose
via the mobile app's "Choose" button (see business_pages.pending_custom_domain), once
they've paid for it - see templates/domain_checkout.html and app.py's paddle_webhook
(the "kind" == "domain" branch), which is the only caller of purchase_domain() below.
Talks to Dynadot (dynadot.com), the registrar this product uses. The domain is charged
directly to the business as its own $15/yr Paddle subscription (see
paddle_billing.domain_price_id) - separate from, and independent of, their Starter/Pro
app subscription - not absorbed by the platform. Renewal on Dynadot should track
whether that domain subscription itself stays paid, not the business's app plan.

is_configured() gates every entry point the same way paddle_billing.is_configured()/
send_sms.sms_configured() do elsewhere in this codebase - missing credentials degrade
to a soft "not configured" failure rather than crashing api_save_page's request.

*** NOT YET SAFE TO ENABLE ***
The actual Dynadot request/response shapes below (endpoint paths, auth header format,
whether pricing needs its own call before registering, DNS-record API shape) have not
been verified against Dynadot's own API documentation - the environment this was
written in has no outbound network access to fetch it. purchase_domain() below refuses
to do anything (returns a clear "not verified yet" failure, spends no money, calls no
API) unless DYNADOT_VERIFIED=true is also set in the environment - so a stray deploy
can never spend real money against an integration nobody has confirmed actually works.
Flip that env var only after a real test purchase has been made and confirmed correct
directly in Dynadot's own dashboard (domain registered, DNS pointed correctly, balance
charged the expected amount) - see the "Automatska kupovina domena" plan for the
verification steps still outstanding.
"""
import os

API_KEY_ENV = "DYNADOT_API_KEY"
API_SECRET_ENV = "DYNADOT_API_SECRET"
VERIFIED_ENV = "DYNADOT_VERIFIED"
_TIMEOUT = 15


def is_configured():
    return bool(os.getenv(API_KEY_ENV) and os.getenv(API_SECRET_ENV))


def _verified():
    return os.getenv(VERIFIED_ENV, "").strip().lower() == "true"


def purchase_domain(domain):
    """domain: a full "name.tld" string, already re-validated by
    domain_lookup.normalize_pending_domain before this is ever called. Returns
    (True, None) on success or (False, reason) on failure - never raises, same
    soft-failure contract as push_notifications.send_push_to_business, since a failed
    domain purchase should degrade to "tell the owner to pick again" rather than 500
    the whole Publish request.

    Nothing below the is_configured()/_verified() guards is implemented yet - see the
    module docstring for why, and for what "verified" means before this can go live."""
    if not is_configured():
        return False, "Domain purchasing isn't configured yet."
    if not _verified():
        return False, "Domain registrar integration hasn't been verified yet - refusing to spend real money on an untested call."

    # TODO once Dynadot's REST API contract is confirmed (see module docstring):
    #   1. Re-check availability/price for `domain` right now (the RDAP check that got
    #      it onto the "available" list in the first place could be stale by the time
    #      Publish is tapped).
    #   2. Register `domain` for 1 year with auto-renew on (renewal should follow this
    #      domain's own Paddle subscription staying paid - see database.
    #      domain_subscription_status - not the business's unrelated app plan).
    #   3. Point its DNS at wherever this platform actually serves custom domains from
    #      (see app.py's Host-header routing, still to be built - Faza 4 of the plan).
    return False, "Not implemented yet."
