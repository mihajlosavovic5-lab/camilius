"""Business-owned public page (camilius.com/p/<slug>): slug format validation and the
reserved-word denylist. Actual persistence lives in database.py
(get_business_page/page_by_slug/upsert_business_page) - this module owns the
non-database rules on top of it, same separation booking.py has for calendar math.
"""
import re
from urllib.parse import urlparse

import booking
from database import page_by_slug

# Lowercase letters/digits, hyphens allowed only between them (no leading/trailing/
# doubled hyphen), 1-50 characters total - readable in a URL, and short enough that a
# business typing their own name in doesn't hit a length surprise. Deliberately no
# uppercase/underscore/unicode: this is also meant to work as a subdomain segment later,
# where those aren't valid at all.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")

# Every current top-level Flask route name, plus a handful reserved for names nothing
# uses yet but that would be confusing (or actively broken) for a business to own: common
# paths a hosting layer or crawler expects to control itself (www, mail, blog, support,
# help), and /p//w//widget/static themselves. /p/<slug> already namespaces business pages
# away from all of these, but reserving them now means a future bare camilius.com/<slug>
# URL can be offered without a scramble to reclaim slugs someone already published under.
RESERVED_SLUGS = frozenset({
    "admin", "api", "app", "avatar", "blog", "check-availability", "connect-instagram",
    "connect-messenger", "connect-whatsapp", "conversations", "delete-account",
    "disconnect-instagram", "disconnect-messenger", "disconnect-whatsapp", "download",
    "export-data", "forgot-password", "googlea7b8cc332250151e", "help",
    "instagram-oauth-callback", "instagram-webhook", "intro", "login", "logout", "mail",
    "messenger-oauth-callback", "messenger-webhook", "notifications", "onboarding", "p",
    "paddle-webhook", "privacy", "profile", "register", "request-tester-access",
    "reset-password", "robots", "schedule", "send", "settings", "site", "sitemap", "static",
    "stripe-webhook", "subscription", "support", "support-chat", "terms",
    "verify-email", "verify-pending", "verify-phone", "w", "whatsapp-webhook", "widget",
    "www",
})


def normalize_slug(raw):
    """Lowercases and strips whitespace only - callers still run the result through
    is_valid_slug before trusting it; this doesn't silently fix an otherwise-invalid slug
    (e.g. one with spaces or symbols in the middle)."""
    return (raw or "").strip().lower()


def is_valid_slug(slug):
    return bool(slug) and bool(_SLUG_RE.match(slug)) and slug not in RESERVED_SLUGS


def slugify(text):
    """A starting suggestion for a slug field - not a validated result on its own, the
    caller still runs this through is_valid_slug before trusting it, same as any other
    slug. Lowercases, collapses anything that isn't a-z0-9 into single hyphens, and trims
    to the same 50-char ceiling _SLUG_RE enforces. Falls back to "my-business" if text has
    no ASCII letters/digits at all (e.g. text written entirely in Cyrillic)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:50].strip("-")
    return slug or "my-business"


def suggest_available_slug(ai_slug, fallback_name, exclude_business_id=None):
    """Turns an AI-suggested slug (e.g. page_generator.generate_page_draft()'s "slug"
    field) into a web address ready to pre-fill a form with: sanitized via slugify()
    above, then only handed back if it's both correctly formatted and not already
    claimed by another business - so a caller never nudges an owner toward an address
    that would just fail with "already taken" the moment they save. Falls back to a
    slugified fallback_name (typically the business's own name) if the AI's suggestion
    is empty or unusable. Returns "" if neither candidate works out, leaving the caller's
    address field exactly as un-opinionated as it was before this suggestion existed."""
    candidates = []
    if (ai_slug or "").strip():
        candidates.append(slugify(ai_slug))
    candidates.append(slugify(fallback_name))
    for candidate in candidates:
        if not is_valid_slug(candidate):
            continue
        existing = page_by_slug(candidate)
        if existing is None or (exclude_business_id is not None and existing[1] == exclude_business_id):
            return candidate
    return ""


def slug_is_available(slug, exclude_business_id=None):
    """Whether `slug` (already validated by the caller via is_valid_slug) is free for
    exclude_business_id to claim - either genuinely unclaimed, or already that exact
    business's own current slug (re-checking/re-saving your own address was never
    "taken" to begin with). Pure database.py:page_by_slug lookup, nothing external -
    this is the platform's own /p/<slug> web address, never a real internet domain
    (see domain_lookup.py for that, entirely separate feature)."""
    existing = page_by_slug(slug)
    return existing is None or (exclude_business_id is not None and existing[1] == exclude_business_id)


def suggest_slug_alternatives(base_slug, exclude_business_id=None, count=3):
    """Called once slug_is_available() above has already said `base_slug` is taken -
    offers up to `count` numbered variants (base-2, base-3, ...) that are actually free
    right now, same availability check as suggest_available_slug uses for its own single
    candidate, just generating several instead of stopping at the first. base_slug is
    trusted to already be validly formatted (the caller's own is_valid_slug check on the
    original address already ran); this only needs to trim it to leave room for a "-N"
    suffix within the 50-char ceiling _SLUG_RE enforces, dropping a trailing hyphen that
    truncation might otherwise leave behind."""
    suggestions = []
    suffix_n = 2
    attempts = 0
    while len(suggestions) < count and attempts < count + 20:
        attempts += 1
        suffix = f"-{suffix_n}"
        suffix_n += 1
        base = base_slug[:50 - len(suffix)].rstrip("-")
        candidate = f"{base}{suffix}"
        if not is_valid_slug(candidate):
            continue
        if slug_is_available(candidate, exclude_business_id=exclude_business_id):
            suggestions.append(candidate)
    return suggestions


# schema.org's dayOfWeek wants a full day name (as a bare token, not a string literal -
# templates interpolate this straight into the "https://schema.org/<Day>" URL), in
# calendar order matching booking.parse_working_hours' 0=Monday..6=Sunday weekday index.
_SCHEMA_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def opening_hours_schema(working_hours_text):
    """working_hours_text (the same free-text column the AI itself reads) turned into a
    list of schema.org OpeningHoursSpecification dicts, one per open day - reuses
    booking.parse_working_hours rather than re-parsing the text a second way, so the
    public page's stated hours can never disagree with what the AI/Schedule page use to
    decide availability. Closed/unparseable days are simply absent, same as they are for
    booking."""
    parsed = booking.parse_working_hours(working_hours_text)
    return [
        {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": f"https://schema.org/{_SCHEMA_DAY_NAMES[weekday]}",
            "opens": open_t.strftime("%H:%M"),
            "closes": close_t.strftime("%H:%M"),
        }
        for weekday, hours in sorted(parsed.items())
        if hours is not None
        for open_t, close_t in [hours]
    ]


def opening_hours_display(working_hours_text):
    """Human-readable hours for the public page itself (as opposed to
    opening_hours_schema's machine-readable JSON-LD form) - every day of the week, in
    order, each paired with "09:00 - 18:00" or "Closed" rather than omitting closed
    days, so a visitor sees a complete week at a glance instead of wondering whether a
    missing day means closed or just not filled in yet."""
    parsed = booking.parse_working_hours(working_hours_text)
    return [
        (
            _SCHEMA_DAY_NAMES[weekday],
            f"{hours[0].strftime('%H:%M')} - {hours[1].strftime('%H:%M')}" if hours else "Closed",
        )
        for weekday, hours in sorted(parsed.items())
    ]


# Same currency list/alias table as static/pickers.js's createPricePicker and
# mobile/app.js - kept in sync by hand since there's no shared module between Python and
# the two JS frontends. Needed here too because price_list_rows() below has to clean up
# old saved prices (free-typed text like "180 dinara", or pre-picker data) the same way
# the picker already does, instead of leaking the raw currency word onto the public page.
_CURRENCIES = frozenset({
    "EUR", "USD", "RSD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "TRY", "RUB", "SEK",
    "NOK", "DKK", "PLN", "AED", "INR", "BRL",
})
_CURRENCY_ALIASES = {
    "din": "RSD", "dinar": "RSD", "dinara": "RSD", "dinari": "RSD", "dinaru": "RSD", "rsd": "RSD",
    "eur": "EUR", "evro": "EUR", "evra": "EUR", "evri": "EUR", "eura": "EUR", "€": "EUR",
    "usd": "USD", "dolar": "USD", "dolara": "USD", "dolari": "USD", "$": "USD",
    "gbp": "GBP", "funti": "GBP", "funta": "GBP", "£": "GBP",
}
_CURRENCY_WORD_RE = re.compile(r"[A-Za-z€$£]+")
_PRICE_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _resolve_currency_word(word):
    """A price word/symbol resolved to one of _CURRENCIES (real code, any case, or one of
    _CURRENCY_ALIASES), or None if it isn't recognized as a currency at all - mirrors
    resolveCurrencyWord() in static/pickers.js exactly."""
    upper = word.upper()
    if upper in _CURRENCIES:
        return upper
    return _CURRENCY_ALIASES.get(word.lower())


def _clean_price_display(raw_price):
    """A saved price string reduced to "<amount> <CODE>" (or just "<amount>" if no
    currency word/symbol is found anywhere in it) for the public page - the bare number
    plus the resolved 3-letter code, with any currency word ("dinara", "evra", ...) or
    symbol dropped rather than shown to a visitor verbatim. Mirrors createPricePicker's
    own parse() in static/pickers.js so newly-picker-saved data (already just "200 RSD")
    and old free-typed data ("200 dinara", "200 dinara RSD") end up looking identical on
    the page. A price with no digits at all (e.g. "price on request") is left untouched -
    it isn't a currency-tagged amount to begin with."""
    if not raw_price:
        return raw_price
    number_match = _PRICE_NUMBER_RE.search(raw_price)
    if not number_match:
        return raw_price
    amount = number_match.group(0).replace(",", ".")
    detected = None
    for word in reversed(_CURRENCY_WORD_RE.findall(raw_price)):
        detected = _resolve_currency_word(word)
        if detected:
            break
    return f"{amount} {detected}" if detected else amount


def price_list_rows(price_list_text):
    """businesses.price_list's raw "Name: Price" (one per line, price optional) text
    turned into a list of {"name": ..., "price": ...} dicts for the public page's
    menu-style row display - one dict per non-blank line, split on the FIRST ":" only
    (a price string could itself contain a colon-adjacent character; splitting on the
    first occurrence rather than a naive str.split(":") avoids mis-splitting on the
    wrong one) so this matches static/pickers.js's own createPriceListPicker parse()
    exactly, the same convention the owner-facing editor already relies on. A line with
    no colon at all becomes {"name": line, "price": ""} - a named item worth listing
    with no price given (e.g. "Consultation - price on request"), not silently dropped.
    The price half is passed through _clean_price_display() so an old currency word like
    "dinara" never reaches the page - see that function for why."""
    rows = []
    for raw_line in (price_list_text or "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        idx = line.find(":")
        if idx == -1:
            rows.append({"name": line, "price": ""})
        else:
            price = _clean_price_display(line[idx + 1:].strip())
            rows.append({"name": line[:idx].strip(), "price": price})
    return rows


# A conservative hostname allowlist (ASCII letters/digits/hyphens, dot-separated
# labels, at least one dot) for normalize_widget_domain below - deliberately stricter
# than what urlparse itself accepts. This value gets dropped straight into a
# Content-Security-Policy header (set_security_headers in app.py) as a bare, unquoted
# source-list token, where a stray ';' starts a new directive and a stray ' ' starts a
# second source - either would let a business (accidentally or not) widen their own
# frame-ancestors beyond the one origin the UI implies they're approving. Requiring a
# real dotted hostname (not just "localhost") is a deliberate simplification, same
# "ship what a real deployed site needs first" scope call as business_pages being one
# page per business - IDN/unicode domains aren't accepted either; a business with one
# can still use its ASCII punycode form.
_HOSTNAME_RE = re.compile(r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))+$")


def normalize_widget_domain(raw):
    """Turns whatever a business types into settings.html's widget-domain field ("
    example.com", "https://www.example.com/", "http://example.com") into a bare
    "https://host[:port]" origin - the exact form app.py's set_security_headers needs to
    drop straight into a CSP frame-ancestors source list. Always forces https: regardless
    of what (or whether) a scheme was typed, since a plaintext-http parent page embedding
    an https iframe is either already broken (mixed content) or a business's mistake, not
    a scheme this product should offer to serve over. Returns None for anything that
    doesn't resolve to a usable, safe host (empty input, too long, a bare path, a scheme
    with no host, a host containing anything outside _HOSTNAME_RE, or an unparseable
    port - urlparse raises ValueError on some malformed input instead of just leaving
    .port empty) - callers treat that as "leave widget_domain unset", the same "no
    domain approved yet" state a business that has never touched this field is already
    in, rather than saving a value that would either 500 this request or break every
    embed attempt against a NULL match anyway."""
    raw = (raw or "").strip()[:255]
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    if not _HOSTNAME_RE.match(host):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    origin = f"https://{host}"
    if port:
        origin += f":{port}"
    return origin
