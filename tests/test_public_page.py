"""Route/template tests for the hosted public page: the settings.html "Your public page"
form, POST /profile/page, GET /p/<slug>, and its sitemap.xml entry. test_pages_db.py
already covers the data layer and slug validation directly - these exercise the actual
HTTP routes and rendered HTML, catching template errors the unit tests can't (this is
exactly how a Jinja "page.slug on a plain tuple" bug was caught while building this)."""
import uuid

import database
from conftest import register_business_web, web_post, get_csrf_token


def _follow(client, response):
    """web_post() always posts with follow_redirects=False (see its own docstring), so
    a test that wants the page a redirect lands on fetches it explicitly instead of
    passing follow_redirects=True to web_post itself - that kwarg collides with the one
    web_post already sets internally."""
    assert response.status_code == 302
    return client.get(response.headers["Location"])


def test_profile_page_renders_with_no_page_saved_yet(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    response = client.get("/profile")
    assert response.status_code == 200
    assert "Your public page" in response.get_data(as_text=True)


def test_save_business_page_then_shows_saved_values(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": slug, "tagline": "Fresh cuts daily",
        "about": "Family-run since 2019.", "published": "on",
    })
    assert response.status_code == 302
    assert response.headers["Location"] == "/profile?message=Your+page+has+been+saved."

    profile = client.get("/profile").get_data(as_text=True)
    assert slug in profile
    assert "Fresh cuts daily" in profile
    assert "Family-run since 2019." in profile


def test_save_business_page_rejects_invalid_slug(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": "Not A Valid Slug!", "tagline": "", "about": "",
    })
    page = _follow(client, response)
    assert page.status_code == 200
    assert "lowercase letters" in page.get_data(as_text=True)
    assert database.get_business_page(database.business_by_username(unique_username)[0]) is None


def test_save_business_page_rejects_reserved_slug(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": "admin", "tagline": "", "about": "",
    })
    page = _follow(client, response)
    assert "lowercase letters" in page.get_data(as_text=True)


def test_save_business_page_rejects_slug_taken_by_another_business(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    taken_slug = f"taken-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, taken_slug, True, None, None)

    other_username = f"{unique_username}_other"
    other_email = f"other-{unique_email}"
    register_business_web(client, other_username, other_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": taken_slug, "tagline": "", "about": "",
    })
    page = _follow(client, response)
    assert "already taken" in page.get_data(as_text=True)


def test_save_business_page_same_business_can_resave_its_own_slug(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/profile")
    web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "tagline": "A", "about": ""})

    csrf_token = get_csrf_token(client, "/profile")
    response = web_post(client, "/profile/page", {"csrf_token": csrf_token, "slug": slug, "tagline": "B", "about": ""})
    page = _follow(client, response)
    assert "already taken" not in page.get_data(as_text=True)
    assert "B" in page.get_data(as_text=True)


def test_public_page_404s_for_unknown_slug(client):
    response = client.get(f"/p/does-not-exist-{uuid.uuid4().hex[:8]}")
    assert response.status_code == 404


def test_public_page_404s_when_not_published(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, "Should not be visible", None)

    response = client.get(f"/p/{slug}")
    assert response.status_code == 404


def test_public_page_renders_when_published(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "We opened in 2019.")

    response = client.get(f"/p/{slug}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Test Salon" in html  # business name, from register_business_web's default
    assert "Fresh cuts daily" in html
    assert "We opened in 2019." in html
    assert "Haircut: 20 EUR" in html  # price_list, from register_business_web's default
    assert "Monday" in html and "09:00 - 18:00" in html  # opening_hours_display
    assert "LocalBusiness" in html  # JSON-LD block present
    assert "https://schema.org/Monday" in html


def test_public_page_is_reachable_by_anyone_without_a_session(client, unique_username, unique_email):
    """The whole point of the page - no login, no session, no cookie required."""
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"anon-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    with client.session_transaction() as sess:
        sess.clear()
    response = client.get(f"/p/{slug}")
    assert response.status_code == 200


def test_sitemap_lists_published_pages_only(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    published_slug = f"pub-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, published_slug, True, None, None)

    other_username = f"{unique_username}_draft"
    other_email = f"draft-{unique_email}"
    register_business_web(client, other_username, other_email)
    draft_business_id = database.business_by_username(other_username)[0]
    draft_slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(draft_business_id, draft_slug, False, None, None)

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert f"/p/{published_slug}" in sitemap
    assert f"/p/{draft_slug}" not in sitemap
