"""Route tests for POST /profile/check-slug - entirely separate from
/profile/check-domains (see test_check_domains_route.py) despite the similar name: this
checks whether a slug is free as THIS platform's own /p/<slug> web address (a plain
database.page_by_slug lookup via pages.slug_is_available/suggest_slug_alternatives),
never a real internet domain and never anything paid."""
import uuid

import database
from conftest import register_business_web, web_post, get_csrf_token


def test_check_slug_available_for_an_unclaimed_slug(client, unique_email):
    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": f"free-{uuid.uuid4().hex[:8]}"})
    assert response.status_code == 200
    assert response.get_json() == {"available": True}


def test_check_slug_available_for_the_callers_own_already_saved_slug(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"mine-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": slug})
    assert response.get_json() == {"available": True}


def test_check_slug_offers_suggestions_when_taken_by_another_business(client, unique_email):
    other_email = f"other-{unique_email}"
    register_business_web(client, other_email)
    taken_slug = f"taken-{uuid.uuid4().hex[:8]}"
    other_business_id = database.business_by_contact_email(other_email)[0]
    database.upsert_business_page(other_business_id, taken_slug, True, None, None)

    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": taken_slug})
    assert response.status_code == 200
    data = response.get_json()
    assert data["available"] is False
    assert len(data["suggestions"]) == 3
    assert all(s.startswith(taken_slug) for s in data["suggestions"])
    assert len(set(data["suggestions"])) == 3  # no duplicates


def test_check_slug_rejects_invalid_format(client, unique_email):
    register_business_web(client, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": "Not A Valid Slug!"})
    assert response.status_code == 400


def test_check_slug_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": "blue-star-cafe"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_check_slug_never_writes_to_the_database(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    csrf_token = get_csrf_token(client, "/profile")

    web_post(client, "/profile/check-slug", {"csrf_token": csrf_token, "slug": f"check-only-{uuid.uuid4().hex[:8]}"})
    assert database.get_business_page(business_id) is None
