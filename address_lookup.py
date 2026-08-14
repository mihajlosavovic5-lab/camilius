"""Free-text address autocomplete suggestions, via OpenStreetMap's Nominatim search API -
no API key, no billing account, unlike Google Places Autocomplete (which needs a paid
Google Cloud API key tied to a registered, billable business - the same blocker that
kept google_places.py off the public page's own Location section, see the polish-pass
plan's Step 5). Best-effort only, same "degrade to nothing" philosophy as
domain_lookup.py's RDAP checks and translate_page's own failure handling - a lookup that
errors, times out, or comes back empty just means no suggestions show, never a broken
field.

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/) caps
anonymous use at roughly one request per second and requires a real identifying
User-Agent - respected here via _REQUEST_HEADERS below and the route's own rate limit
(api.py's api_address_suggest)."""
import requests

_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
_TIMEOUT = 4
_REQUEST_HEADERS = {"User-Agent": "Camilius-AddressLookup/1.0 (https://app.ai-auto-reply-mail.com)"}
# Below this, a query is still someone typing their first few characters - never worth a
# network round trip (Nominatim would just return noise anyway).
_MIN_QUERY_LENGTH = 3
_MAX_RESULTS = 5


def suggest_addresses(query, limit=_MAX_RESULTS):
    """query: whatever a visitor has typed so far into an address field. Returns a plain
    list of full display-name strings (e.g. "Kneza Miloša 12, Kragujevac, Šumadija
    District, 34000, Serbia"), ready to show as picks - [] for a too-short query, a
    failed/timed-out request, or one that matched nothing. Never raises."""
    query = (query or "").strip()
    if len(query) < _MIN_QUERY_LENGTH:
        return []
    try:
        response = requests.get(
            _SEARCH_URL,
            params={"q": query, "format": "jsonv2", "addressdetails": 0, "limit": limit},
            headers=_REQUEST_HEADERS,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(results, list):
        return []
    return [item["display_name"] for item in results if isinstance(item, dict) and item.get("display_name")]
