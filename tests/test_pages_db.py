"""Tests for the business_pages data layer (database.py) and slug validation (pages.py) -
independent of the routes/templates that will sit on top of them."""
import uuid

import pytest

import database
import pages
from conftest import register_and_login


# --- pages.py: slug validation ---

@pytest.mark.parametrize("slug", ["marko", "marko-barber", "salon123", "a", "a1", "x" * 50])
def test_valid_slugs_accepted(slug):
    assert pages.is_valid_slug(slug) is True


@pytest.mark.parametrize("slug", [
    "", None, "Marko",  # uppercase
    "-marko", "marko-",  # leading/trailing hyphen
    "marko_barber",  # underscore not allowed
    "mar ko",  # space
    "x" * 51,  # too long
])
def test_invalid_slugs_rejected(slug):
    assert pages.is_valid_slug(slug) is False


def test_reserved_slugs_rejected():
    for reserved in ("admin", "login", "register", "static", "p", "settings", "www"):
        assert pages.is_valid_slug(reserved) is False


def test_normalize_slug_lowercases_and_strips():
    assert pages.normalize_slug("  Marko-Barber  ") == "marko-barber"
    assert pages.normalize_slug(None) == ""


# --- database.py: business_pages CRUD ---

def test_get_business_page_returns_none_when_never_saved(client, unique_username, unique_email):
    _, business_id = register_and_login(client, unique_username, unique_email)
    assert database.get_business_page(business_id) is None
    assert database.page_by_slug(f"nonexistent-{uuid.uuid4().hex[:8]}") is None


def test_upsert_business_page_creates_then_updates(client, unique_username, unique_email):
    _, business_id = register_and_login(client, unique_username, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"

    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We've been open since 2019.")
    page = database.get_business_page(business_id)
    assert page[2] == slug
    assert page[3] is False
    assert page[4] == "Fresh cuts daily"

    database.upsert_business_page(business_id, slug, True, "Updated tagline", "Updated about.")
    page = database.get_business_page(business_id)
    assert page[3] is True
    assert page[4] == "Updated tagline"
    # Still exactly one row for this business - the whole point of the ON CONFLICT (business_id) upsert.
    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM business_pages WHERE business_id = %s", (business_id,))
    assert cursor.fetchone()[0] == 1
    connection.close()


def test_upsert_business_page_slug_collision_raises(client, unique_username, unique_email):
    _, business_id_a = register_and_login(client, unique_username, unique_email)
    other_username = f"{unique_username}_b"
    other_email = f"b-{unique_email}"
    _, business_id_b = register_and_login(client, other_username, other_email)

    slug = f"shared-slug-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id_a, slug, True, None, None)

    with pytest.raises(database.UniqueViolation):
        database.upsert_business_page(business_id_b, slug, True, None, None)


def test_upsert_business_page_same_business_can_keep_its_own_slug(client, unique_username, unique_email):
    """Saving the form again without changing the slug must not trip the collision check
    against the business's own existing row."""
    _, business_id = register_and_login(client, unique_username, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "A", "B")
    database.upsert_business_page(business_id, slug, True, "A2", "B2")  # must not raise
    assert database.get_business_page(business_id)[4] == "A2"


def test_page_by_slug_finds_it_regardless_of_published_state(client, unique_username, unique_email):
    _, business_id = register_and_login(client, unique_username, unique_email)
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)
    found = database.page_by_slug(slug)
    assert found is not None
    assert found[1] == business_id
    assert found[3] is False  # unpublished - route decides whether that means 404


def test_published_page_slugs_only_returns_published(client, unique_username, unique_email):
    _, business_id_a = register_and_login(client, unique_username, unique_email)
    other_username = f"{unique_username}_pub"
    other_email = f"pub-{unique_email}"
    _, business_id_b = register_and_login(client, other_username, other_email)

    published_slug = f"pub-{uuid.uuid4().hex[:8]}"
    draft_slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id_a, published_slug, True, None, None)
    database.upsert_business_page(business_id_b, draft_slug, False, None, None)

    slugs = [row[0] for row in database.published_page_slugs()]
    assert published_slug in slugs
    assert draft_slug not in slugs


def test_delete_business_removes_its_page(client, unique_username, unique_email):
    _, business_id = register_and_login(client, unique_username, unique_email)
    slug = f"deleteme-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)
    assert database.page_by_slug(slug) is not None

    database.delete_business(business_id)
    assert database.page_by_slug(slug) is None
