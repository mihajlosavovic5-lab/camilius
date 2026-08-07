"""Checks whether real, independently-registrable domain names (e.g. blue-star-cafe.com)
are taken, via RDAP - the modern, JSON-over-HTTPS successor to raw port-43 WHOIS. No API
key, no account, no purchasing capability: this only answers "does someone already own
this", nothing more. Distinct from pages.py's own slug - that's a path on THIS platform
(camilius.com/p/<slug>), never a domain an owner could register and point anywhere else.

Uses rdap.org's free bootstrap redirector rather than talking to each TLD's registry
RDAP server directly - it looks up the right server from IANA's registry and forwards the
request, so this doesn't need its own copy of that TLD->server mapping. Best-effort: many
ccTLD registries (including .rs, at the time of writing) don't run an RDAP server at all,
so a lookup for those comes back "unknown" rather than a guess - callers show that as
"couldn't check", never as available or taken.
"""
import requests

# Serial round trips (see check_domains) must stay well short of turning "check domains"
# into a multi-second stall - 5s per candidate is already a generous worst case for a
# single RDAP lookup.
_RDAP_TIMEOUT = 5

# .com/.net are the two RDAP-backed gTLDs a Serbian small business is most likely to
# actually want; .rs itself isn't RDAP-checkable (see module docstring) but is kept as a
# candidate anyway - check_domain's own "unknown" result still surfaces it to the owner
# as "couldn't check automatically, verify at rnids.rs" rather than silently omitting it.
DEFAULT_TLDS = [".com", ".net", ".rs"]

AVAILABLE = "available"
TAKEN = "taken"
UNKNOWN = "unknown"


def check_domain(domain):
    """domain: a full "name.tld" string, e.g. "blue-star-cafe.com". Returns AVAILABLE,
    TAKEN, or UNKNOWN (the RDAP request errored, timed out, or the TLD's registry has no
    RDAP server for rdap.org to bootstrap to) - never a guessed answer."""
    try:
        response = requests.get(f"https://rdap.org/domain/{domain}", timeout=_RDAP_TIMEOUT)
    except requests.RequestException:
        return UNKNOWN
    if response.status_code == 404:
        return AVAILABLE
    if response.status_code == 200:
        return TAKEN
    return UNKNOWN


def check_domains(base_name, tlds=None):
    """base_name: a slug-shaped name with no TLD (e.g. page_generator's AI-suggested
    page slug, "blue-star-cafe") - reused as-is, not re-slugified, since callers already
    have a clean candidate (pages.slugify's output, or an owner's own web-address field).
    Returns [{"domain": "blue-star-cafe.com", "status": AVAILABLE|TAKEN|UNKNOWN}, ...],
    one entry per tld (DEFAULT_TLDS if not given) - [] if base_name is blank."""
    base_name = (base_name or "").strip().strip(".")
    if not base_name:
        return []
    return [
        {"domain": f"{base_name}{tld}", "status": check_domain(f"{base_name}{tld}")}
        for tld in (tlds or DEFAULT_TLDS)
    ]
