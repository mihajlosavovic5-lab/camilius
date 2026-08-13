"""Tests for POST /site/theme - the "Change theme" picker rendered directly on the
owner's own site_preview() view (see public_page.html's own {% if theme_choices %}
owner toolbar and database.set_business_page_theme). Unlike /profile/suggest-themes
(ai_theme.py's freeform AI hex palettes), this only ever accepts one of the classic
themes.THEMES preset ids."""
import uuid

import database
import themes
from conftest import register_business_web, web_post, get_csrf_token


def test_set_site_theme_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = web_post(client, "/site/theme", {"csrf_token": csrf_token, "theme_id": "ocean"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_set_site_theme_404s_with_no_page_saved_yet(client, unique_email):
    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/site/theme", {"csrf_token": csrf_token, "theme_id": "ocean"})
    assert response.status_code == 404


def test_set_site_theme_rejects_unknown_theme_id(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    database.upsert_business_page(business_id, f"slug-{uuid.uuid4().hex[:8]}", True, None, None)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/site/theme", {"csrf_token": csrf_token, "theme_id": "not-a-real-theme"})
    assert response.status_code == 400
    assert database.get_business_page(business_id)[9] == themes.DEFAULT_THEME_ID


def test_set_site_theme_updates_theme_and_clears_custom_json(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    database.upsert_business_page(
        business_id, f"slug-{uuid.uuid4().hex[:8]}", True, None, None,
        theme=themes.CUSTOM_THEME_ID, custom_theme_json='{"base": {}}',
    )
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/site/theme", {"csrf_token": csrf_token, "theme_id": "ocean"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "theme_id": "ocean"}

    page = database.get_business_page(business_id)
    assert page[9] == "ocean"
    assert page[10] is None


def test_set_site_theme_only_affects_own_business(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    database.upsert_business_page(business_id, f"slug-{uuid.uuid4().hex[:8]}", True, None, None)

    other_email = f"other-{unique_email}"
    register_business_web(client, other_email)
    other_business_id = database.business_by_contact_email(other_email)[0]
    database.upsert_business_page(other_business_id, f"slug-{uuid.uuid4().hex[:8]}", True, None, None)

    # register_business_web's own login now belongs to the second business it just
    # created - switch the session back to the first to actually test isolation.
    with client.session_transaction() as sess:
        sess["business_id"] = business_id
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/site/theme", {"csrf_token": csrf_token, "theme_id": "mint"})
    assert response.status_code == 200

    assert database.get_business_page(business_id)[9] == "mint"
    assert database.get_business_page(other_business_id)[9] == themes.DEFAULT_THEME_ID


def test_site_preview_includes_owner_theme_picker(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    database.upsert_business_page(business_id, f"slug-{uuid.uuid4().hex[:8]}", True, None, None)

    html = client.get("/site/preview").get_data(as_text=True)
    assert 'id="theme-fab"' in html
    assert 'id="theme-panel"' in html
    # The full curated catalog is embedded for the picker's grid, not just the current theme.
    for theme_id in themes.THEME_IDS:
        assert f'"{theme_id}"' in html


def test_public_page_shows_owner_theme_picker_when_owner_is_logged_in(client, unique_email):
    """The mobile app's own "View page" button opens the real /p/<slug> URL directly
    (not the session-only /site/preview route) once a page is published - see
    mobile/app.js's openSitePreview/openLiveSite split - so the owner's own logged-in
    session hitting this exact route must also get their theme picker, not just
    /site/preview's unpublished-page fallback."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert 'id="theme-fab"' in html
    assert 'id="theme-panel"' in html


def test_public_page_hides_theme_picker_from_anonymous_visitors(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    with client.session_transaction() as sess:
        sess.clear()
    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert 'id="theme-fab"' not in html
    assert 'id="theme-panel"' not in html


def test_public_page_hides_theme_picker_from_a_different_logged_in_business(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    other_email = f"other-{unique_email}"
    register_business_web(client, other_email)  # switches this client's session to the new business

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert 'id="theme-fab"' not in html
    assert 'id="theme-panel"' not in html
