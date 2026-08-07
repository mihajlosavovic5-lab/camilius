"""Checks whether real, independently-registrable domain names (e.g. blue-star-cafe.com)
are taken, via RDAP - the modern, JSON-over-HTTPS successor to raw port-43 WHOIS. No API
key, no account, no purchasing capability: this only answers "does someone already own
this", nothing more. Distinct from pages.py's own slug - that's a path on THIS platform
(camilius.com/p/<slug>), never a domain an owner could register and point anywhere else.

Uses rdap.org's free bootstrap redirector rather than talking to each TLD's registry
RDAP server directly - it looks up the right server from IANA's registry and forwards the
request, so this doesn't need its own copy of that TLD->server mapping. Best-effort: many
ccTLD registries (including .rs, at the time of writing) don't run an RDAP server at all -
_RDAP_SUPPORTED_TLDS below is what actually enforces that (rather than just documenting
it and hoping): a TLD outside it never even makes a request, since rdap.org's own 404 for
"I don't know this TLD at all" is indistinguishable from a real registry's 404 for "this
specific domain isn't registered" - treating the former as AVAILABLE would be a guess
dressed up as a real answer. Callers show UNKNOWN as "couldn't check", never as available
or taken.
"""
import requests

# Serial round trips (see check_domains) must stay well short of turning "check domains"
# into a multi-second stall - 5s per candidate is already a generous worst case for a
# single RDAP lookup.
_RDAP_TIMEOUT = 5

# rdap.org itself expects this (some public RDAP endpoints reject/rate-limit anonymous
# requests with no UA harder than ones that identify themselves).
_REQUEST_HEADERS = {"User-Agent": "Camilius-DomainCheck/1.0", "Accept": "application/rdap+json"}

# TLDs actually known to run a real RDAP server (Verisign, for both) - see module
# docstring for why this allowlist exists at all, not just .com/.net being the ones a
# Serbian small business is most likely to want. Extend this only after confirming a new
# TLD's registry genuinely publishes RDAP (check https://data.iana.org/rdap/dns.json),
# not just because a business asked for it.
_RDAP_SUPPORTED_TLDS = frozenset({".com", ".net"})

# .rs itself isn't in _RDAP_SUPPORTED_TLDS (so never live-checked, always UNKNOWN) but is
# kept as a default candidate anyway - still worth surfacing to the owner as "couldn't
# check automatically, verify at rnids.rs" rather than silently omitting it.
DEFAULT_TLDS = [".com", ".net", ".rs"]

AVAILABLE = "available"
TAKEN = "taken"
UNKNOWN = "unknown"


def _tld_of(domain):
    return domain[domain.index("."):] if "." in domain else ""


def check_domain(domain):
    """domain: a full "name.tld" string, e.g. "blue-star-cafe.com". Returns UNKNOWN
    immediately, with no network call, for any TLD outside _RDAP_SUPPORTED_TLDS -
    otherwise AVAILABLE, TAKEN, or UNKNOWN (the RDAP request errored, timed out, or
    returned neither a clean 404 nor 200) - never a guessed answer."""
    if _tld_of(domain) not in _RDAP_SUPPORTED_TLDS:
        return UNKNOWN
    try:
        response = requests.get(f"https://rdap.org/domain/{domain}", headers=_REQUEST_HEADERS, timeout=_RDAP_TIMEOUT)
    except requests.RequestException:
        return UNKNOWN
    if response.status_code == 404:
        return AVAILABLE
    if response.status_code == 200:
        return TAKEN
    return UNKNOWN


def check_domains(base_name, tlds=None):
    """base_name: nominally a slug-shaped name with no TLD (e.g. page_generator's
    AI-suggested page slug, "blue-star-cafe"), but callers include a free-text web-
    address field an owner can type anything into - including, reasonably enough, a
    full domain like "blue-star-cafe.com" they already have in mind. Only the part
    before the first "." is ever used, so that case checks "blue-star-cafe.com/.net/.rs"
    the same as a bare "blue-star-cafe" would, instead of building nonsense candidates
    like "blue-star-cafe.com.com". Returns
    [{"domain": "blue-star-cafe.com", "status": AVAILABLE|TAKEN|UNKNOWN}, ...], one entry
    per tld (DEFAULT_TLDS if not given) - [] if nothing's left after that split."""
    base_name = (base_name or "").strip().strip(".").split(".", 1)[0].strip("-")
    if not base_name:
        return []
    return [
        {"domain": f"{base_name}{tld}", "status": check_domain(f"{base_name}{tld}")}
        for tld in (tlds or DEFAULT_TLDS)
    ]
