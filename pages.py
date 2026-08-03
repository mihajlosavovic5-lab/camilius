"""Business-owned public page (camilius.com/p/<slug>): slug format validation and the
reserved-word denylist. Actual persistence lives in database.py
(get_business_page/page_by_slug/upsert_business_page) - this module owns the
non-database rules on top of it, same separation booking.py has for calendar math.
"""
import re

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
