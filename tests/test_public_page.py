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


def test_profile_page_renders_with_no_page_saved_yet(client, unique_email):
    register_business_web(client, unique_email)
    response = client.get("/profile")
    assert response.status_code == 200
    assert "Your public page" in response.get_data(as_text=True)


def test_save_business_page_then_shows_saved_values(client, unique_email):
    register_business_web(client, unique_email)
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


def test_save_business_page_rejects_invalid_slug(client, unique_email):
    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": "Not A Valid Slug!", "tagline": "Fresh cuts daily", "about": "",
    })
    # Re-rendered in place (not a redirect) so the business's own typed values - including
    # the invalid slug itself - are still on screen next to the error, instead of a fresh
    # GET wiping the form back to whatever (if anything) was last actually saved.
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "lowercase letters" in page
    assert "not a valid slug!" in page  # normalize_slug lowercases before re-rendering
    assert "Fresh cuts daily" in page
    assert database.get_business_page(database.business_by_contact_email(unique_email)[0]) is None


def test_save_business_page_rejects_reserved_slug(client, unique_email):
    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": "admin", "tagline": "", "about": "",
    })
    assert response.status_code == 200
    assert "lowercase letters" in response.get_data(as_text=True)


def test_save_business_page_rejects_slug_taken_by_another_business(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    taken_slug = f"taken-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, taken_slug, True, None, None)

    other_email = f"other-{unique_email}"
    register_business_web(client, other_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": taken_slug, "tagline": "", "about": "",
    })
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "already taken" in page
    assert taken_slug in page


def test_save_business_page_same_business_can_resave_its_own_slug(client, unique_email):
    register_business_web(client, unique_email)
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


def test_public_page_404s_when_not_published(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, "Should not be visible", None)

    response = client.get(f"/p/{slug}")
    assert response.status_code == 404


def test_public_page_renders_when_published(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "We opened in 2019.")

    response = client.get(f"/p/{slug}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Test Salon" in html  # business name, from register_business_web's default
    assert "Fresh cuts daily" in html
    assert "We opened in 2019." in html
    assert "Haircut: 20 EUR" in html  # price_list, from register_business_web's default
    assert "LocalBusiness" in html  # JSON-LD block present
    assert "https://schema.org/Monday" in html


def test_public_page_title_renders_pre_split_server_side(client, unique_email):
    """The top-bar title/page header used to render as one plain line of text and only
    get restructured into .title-main/.title-sub spans by client JS after the page
    loaded - visible as a flash from the raw "Name - Location" line into the split,
    two-tier display once that script ran. pages.split_display_title now does the same
    split server-side so the first byte of HTML is already correct - see app.py's
    _render_public_page/title_main/title_sub and the template's own comment on why the
    initial splitTitleEls JS pass was removed."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Kafeterija Aroma - Novi Sad", None)

    response = client.get(f"/p/{slug}")
    html = response.get_data(as_text=True)
    assert '<div class="top-bar-title" data-i18n-content="tagline">'\
        '<span class="title-main">Kafeterija Aroma</span><span class="title-sub">Novi Sad</span></div>' in html
    assert '<h1 data-i18n-content="tagline">'\
        '<span class="title-main">Kafeterija Aroma</span><span class="title-sub">Novi Sad</span></h1>' in html


def test_public_page_hides_badge_during_active_trial(client, unique_email):
    """F4: every business gets a real 14-day trial on signup (add_business's own
    initial_status="trialing" default) - the "Powered by Camilius" badge is gone from
    day one, same as has_pro_access() already treats a self-serve trial as full access,
    not just a paid plan."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"trial-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    response = client.get(f"/p/{slug}")
    assert "Powered by Camilius" not in response.get_data(as_text=True)


def test_public_page_shows_badge_once_trial_expired_with_no_paid_plan(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"expired-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET created_at = created_at - INTERVAL '30 days' WHERE id = %s", (business_id,))
    connection.commit()
    connection.close()
    assert database.effective_subscription_active(business_id) is False

    response = client.get(f"/p/{slug}")
    assert "Powered by Camilius" in response.get_data(as_text=True)


def test_public_page_hides_badge_for_a_real_paid_subscription(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"paid-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)
    database.link_subscription(business_id, "cust_1", "sub_1", "active", plan="pro")

    response = client.get(f"/p/{slug}")
    assert "Powered by Camilius" not in response.get_data(as_text=True)


def test_public_page_is_reachable_by_anyone_without_a_session(client, unique_email):
    """The whole point of the page - no login, no session, no cookie required."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"anon-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    with client.session_transaction() as sess:
        sess.clear()
    response = client.get(f"/p/{slug}")
    assert response.status_code == 200


def test_sitemap_lists_published_pages_only(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    published_slug = f"pub-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, published_slug, True, None, None)

    other_email = f"draft-{unique_email}"
    register_business_web(client, other_email)
    draft_business_id = database.business_by_contact_email(other_email)[0]
    draft_slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(draft_business_id, draft_slug, False, None, None)

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert f"/p/{published_slug}" in sitemap
    assert f"/p/{draft_slug}" not in sitemap


def test_site_preview_requires_login(client):
    response = client.get("/site/preview")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_site_preview_404s_with_no_page_saved_yet(client, unique_email):
    register_business_web(client, unique_email)
    response = client.get("/site/preview")
    assert response.status_code == 404


def test_site_preview_renders_unpublished_page(client, unique_email):
    """The whole point of this route (see app.py's site_preview docstring): unlike
    /p/<slug>, an unpublished page still renders here so the owner can see the complete
    site Camilius built before ever choosing to publish it."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We opened in 2019.")

    response = client.get("/site/preview")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Test Salon" in html
    assert "Fresh cuts daily" in html
    assert "We opened in 2019." in html
    assert 'name="robots" content="noindex, nofollow"' in html  # preview=True, never indexed


def test_site_preview_renders_published_page_too(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)

    response = client.get("/site/preview")
    assert response.status_code == 200
    assert "Fresh cuts daily" in response.get_data(as_text=True)


def test_public_page_is_never_cached(client, unique_email):
    """The mobile app's "View page" button reopens this same URL in the same native
    in-app browser tab (Capacitor's Browser plugin) after every edit - without an
    explicit no-store, a repeat tap could repaint for a moment from that tab's own
    cached/bfcached snapshot, flashing the pre-edit title before the fresh response
    replaces it. See app.py's no_store_public_page docstring.

    Uses a separate, never-logged-in client - this is the real "View page" scenario
    (openLiveSite in mobile/app.js opens /p/<slug> directly, no session cookie ever
    gets set) - not the owner's own logged-in web session, which already gets a
    no-store Cache-Control from no_store_authenticated_pages regardless of this fix."""
    import app as app_module

    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"live-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)

    anon_client = app_module.app.test_client()
    response = anon_client.get(f"/p/{slug}")
    assert response.headers["Cache-Control"] == "no-store, must-revalidate"
