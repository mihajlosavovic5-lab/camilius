"""Business-owned public page (camilius.com/p/<slug>): slug format validation and the
reserved-word denylist. Actual persistence lives in database.py
(get_business_page/page_by_slug/upsert_business_page) - this module owns the
non-database rules on top of it, same separation booking.py has for calendar math.
"""
import re
from urllib.parse import urlparse

import booking

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
    "messenger-oauth-callback", "messenger-webhook", "notifications", "p",
    "paddle-webhook", "privacy", "profile", "register", "request-tester-access",
    "reset-password", "robots", "schedule", "send", "settings", "sitemap", "static",
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


def normalize_widget_domain(raw):
    """Turns whatever a business types into settings.html's widget-domain field ("
    example.com", "https://www.example.com/", "http://example.com") into a bare
    "https://host[:port]" origin - the exact form app.py's set_security_headers needs to
    drop straight into a CSP frame-ancestors source list. Always forces https: regardless
    of what (or whether) a scheme was typed, since a plaintext-http parent page embedding
    an https iframe is either already broken (mixed content) or a business's mistake, not
    a scheme this product should offer to serve over. Returns None for anything that
    doesn't resolve to a usable host (empty input, a bare path, a scheme with no host) -
    callers treat that as "leave widget_domain unset", the same "no domain approved yet"
    state a business that has never touched this field is already in, rather than saving
    a value that would just make every embed attempt fail against a NULL match anyway."""
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname
    if not host:
        return None
    origin = f"https://{host}"
    if parsed.port:
        origin += f":{parsed.port}"
    return origin
