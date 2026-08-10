"""Tests for F5 - the embeddable widget: pages.normalize_widget_domain, the
GET /w/<slug> iframe route, GET /widget.js loader, saving widget_domain through
POST /profile/page, and the CSP frame-ancestors/X-Frame-Options split
set_security_headers (app.py) applies only to /w/<slug> responses - the actual security
boundary controlling who's allowed to embed a business's widget at all."""
import uuid

import database
import pages
from conftest import register_business_web, web_post, get_csrf_token


def _publish_page(client, email, widget_domain=None):
    register_business_web(client, email)
    business_id = database.business_by_contact_email(email)[0]
    slug = f"widget-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "Since 2019.", widget_domain)
    return business_id, slug


# --- pages.normalize_widget_domain ---

def test_normalize_widget_domain_accepts_bare_host():
    assert pages.normalize_widget_domain("example.com") == "https://example.com"


def test_normalize_widget_domain_forces_https_regardless_of_input_scheme():
    assert pages.normalize_widget_domain("http://example.com") == "https://example.com"
    assert pages.normalize_widget_domain("https://example.com") == "https://example.com"


def test_normalize_widget_domain_strips_path_and_trailing_slash():
    assert pages.normalize_widget_domain("https://example.com/some/path") == "https://example.com"
    assert pages.normalize_widget_domain("example.com/") == "https://example.com"


def test_normalize_widget_domain_keeps_a_nonstandard_port():
    assert pages.normalize_widget_domain("example.com:8080") == "https://example.com:8080"


def test_normalize_widget_domain_returns_none_for_unusable_input():
    assert pages.normalize_widget_domain("") is None
    assert pages.normalize_widget_domain("   ") is None
    assert pages.normalize_widget_domain(None) is None
    assert pages.normalize_widget_domain("https://") is None


# Regression coverage for a real bug found in review: urlparse() alone is far more
# permissive than a CSP frame-ancestors source-list token can safely be - a stray ';'
# or ' ' in the "hostname" starts a second directive/source, and a malformed port
# (e.g. a value with trailing junk after a colon) makes urlparse's own .port property
# raise ValueError instead of returning None, which crashed save_business_page() with
# an unhandled 500 before _HOSTNAME_RE was added. None of this is exploitable
# cross-tenant (only an authenticated business can set its own widget_domain), but a
# malformed value here would either 500 the request or quietly widen that business's
# own frame-ancestors beyond the single origin the UI implies it's approving.
def test_normalize_widget_domain_rejects_csp_injection_attempts():
    assert pages.normalize_widget_domain("evil.com;frame-ancestors *") is None
    assert pages.normalize_widget_domain("evil.com evil2.com") is None
    assert pages.normalize_widget_domain('evil.com"; script-src *; "') is None


def test_normalize_widget_domain_never_raises_on_malformed_port():
    # urlparse(...).port raises ValueError here rather than returning None - this used
    # to propagate straight out of normalize_widget_domain and 500 the request.
    assert pages.normalize_widget_domain("evil.com:80\r\nX-Foo:1") is None
    assert pages.normalize_widget_domain("evil.com:notaport") is None


def test_normalize_widget_domain_rejects_control_characters():
    assert pages.normalize_widget_domain("evil.com\r\nSet-Cookie: x=y") is None
    assert pages.normalize_widget_domain("evil.com\nX-Injected: 1") is None


def test_normalize_widget_domain_rejects_a_bare_hostname_with_no_dot():
    assert pages.normalize_widget_domain("localhost") is None


def test_normalize_widget_domain_caps_length_instead_of_storing_unbounded_input():
    huge = "a" * 5000
    assert pages.normalize_widget_domain(huge) is None


# --- POST /profile/page saves widget_domain ---

def test_save_business_page_does_not_500_on_malformed_widget_domain(client, unique_email):
    """Route-level regression test for the same bug the unit tests above cover directly
    - this is what actually failed (a raw 500) before normalize_widget_domain stopped
    letting urlparse's ValueError escape."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"slug-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": slug, "widget_domain": "evil.com:80\r\nX-Foo:1",
    })
    assert response.status_code == 302  # normal save-success redirect, not a 500
    assert database.get_business_page(business_id)[8] is None


def test_save_business_page_persists_normalized_widget_domain(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"slug-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")

    web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": slug, "tagline": "", "about": "",
        "widget_domain": "http://Example.com/embed",
    })
    page = database.get_business_page(business_id)
    assert page[8] == "https://example.com"


def test_save_business_page_clears_widget_domain_when_left_blank(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"slug-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")
    web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "widget_domain": "example.com"})
    assert database.get_business_page(business_id)[8] == "https://example.com"

    csrf_token = get_csrf_token(client, "/profile")
    web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "widget_domain": ""})
    assert database.get_business_page(business_id)[8] is None


# --- GET /w/<slug> ---

def test_widget_frame_404s_for_unknown_or_unpublished_slug(client, unique_email):
    assert client.get(f"/w/does-not-exist-{uuid.uuid4().hex[:8]}").status_code == 404

    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)
    assert client.get(f"/w/{slug}").status_code == 404


def test_widget_frame_renders_and_denies_framing_with_no_domain_approved(client, unique_email):
    _, slug = _publish_page(client, unique_email, widget_domain=None)

    response = client.get(f"/w/{slug}")
    assert response.status_code == 200
    assert "Test Salon" in response.get_data(as_text=True)
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in response.headers


def test_widget_frame_allows_framing_from_the_approved_domain(client, unique_email):
    _, slug = _publish_page(client, unique_email, widget_domain="https://example.com")

    response = client.get(f"/w/{slug}")
    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self' https://example.com" in csp
    assert "X-Frame-Options" not in response.headers


def test_normal_routes_keep_same_origin_framing_only(client):
    """Regression check: the /w/<slug> special-case in set_security_headers must not
    leak into every other response - everything else keeps the original SAMEORIGIN
    behavior unchanged."""
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" not in response.headers["Content-Security-Policy"]


# --- GET /widget.js ---

def test_widget_loader_js_is_served_as_javascript(client):
    response = client.get("/widget.js")
    assert response.status_code == 200
    assert response.mimetype == "application/javascript"
    body = response.get_data(as_text=True)
    assert "document.currentScript" in body
    assert "/w/" in body
