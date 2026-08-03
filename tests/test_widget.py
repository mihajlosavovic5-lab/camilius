"""Tests for F5 - the embeddable widget: pages.normalize_widget_domain, the
GET /w/<slug> iframe route, GET /widget.js loader, saving widget_domain through
POST /profile/page, and the CSP frame-ancestors/X-Frame-Options split
set_security_headers (app.py) applies only to /w/<slug> responses - the actual security
boundary controlling who's allowed to embed a business's widget at all."""
import uuid

import database
import pages
from conftest import register_business_web, web_post, get_csrf_token


def _publish_page(client, username, email, widget_domain=None):
    register_business_web(client, username, email)
    business_id = database.business_by_username(username)[0]
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


# --- POST /profile/page saves widget_domain ---

def test_save_business_page_persists_normalized_widget_domain(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"slug-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")

    web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": slug, "tagline": "", "about": "",
        "widget_domain": "http://Example.com/embed",
    })
    page = database.get_business_page(business_id)
    assert page[8] == "https://example.com"


def test_save_business_page_clears_widget_domain_when_left_blank(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"slug-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")
    web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "widget_domain": "example.com"})
    assert database.get_business_page(business_id)[8] == "https://example.com"

    csrf_token = get_csrf_token(client, "/profile")
    web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "widget_domain": ""})
    assert database.get_business_page(business_id)[8] is None


# --- GET /w/<slug> ---

def test_widget_frame_404s_for_unknown_or_unpublished_slug(client, unique_username, unique_email):
    assert client.get(f"/w/does-not-exist-{uuid.uuid4().hex[:8]}").status_code == 404

    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)
    assert client.get(f"/w/{slug}").status_code == 404


def test_widget_frame_renders_and_denies_framing_with_no_domain_approved(client, unique_username, unique_email):
    _, slug = _publish_page(client, unique_username, unique_email, widget_domain=None)

    response = client.get(f"/w/{slug}")
    assert response.status_code == 200
    assert "Test Salon" in response.get_data(as_text=True)
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in response.headers


def test_widget_frame_allows_framing_from_the_approved_domain(client, unique_username, unique_email):
    _, slug = _publish_page(client, unique_username, unique_email, widget_domain="https://example.com")

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
