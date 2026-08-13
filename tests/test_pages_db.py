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


# --- pages.py: slugify / suggest_available_slug ---

def test_slugify_lowercases_and_collapses_non_ascii():
    assert pages.slugify("Blue Star Café!") == "blue-star-caf"
    assert pages.slugify("  Marko's  Barbershop  ") == "marko-s-barbershop"


def test_slugify_falls_back_to_my_business_for_no_ascii_chars():
    assert pages.slugify("Кафић Плава Звезда") == "my-business"
    assert pages.slugify("") == "my-business"
    assert pages.slugify(None) == "my-business"


def test_slugify_trims_to_fifty_chars():
    assert len(pages.slugify("x" * 100)) == 50


def test_suggest_available_slug_uses_sanitized_ai_slug_when_available():
    unique = uuid.uuid4().hex[:8]
    result = pages.suggest_available_slug(f"The Blue Scissors {unique}", "Fallback Name")
    assert result == f"the-blue-scissors-{unique}"


def test_suggest_available_slug_falls_back_to_name_when_ai_slug_blank():
    unique = uuid.uuid4().hex[:8]
    result = pages.suggest_available_slug("", f"Fallback Name {unique}")
    assert result == f"fallback-name-{unique}"


def test_suggest_available_slug_falls_back_to_name_when_ai_slug_taken(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    unique = uuid.uuid4().hex[:8]
    taken = f"taken-{unique}"
    database.upsert_business_page(business_id, taken, True, "T", "A")

    result = pages.suggest_available_slug(taken, f"Fallback Name {unique}")
    assert result == f"fallback-name-{unique}"


def test_suggest_available_slug_returns_empty_when_nothing_available(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    unique = uuid.uuid4().hex[:8]
    taken = f"taken-{unique}"
    database.upsert_business_page(business_id, taken, True, "T", "A")

    result = pages.suggest_available_slug(taken, taken)
    assert result == ""


def test_suggest_available_slug_excludes_own_business(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    unique = uuid.uuid4().hex[:8]
    own_slug = f"my-own-slug-{unique}"
    database.upsert_business_page(business_id, own_slug, True, "T", "A")

    result = pages.suggest_available_slug(own_slug, "Fallback Name", exclude_business_id=business_id)
    assert result == own_slug


# --- pages.slug_is_available / suggest_slug_alternatives (settings.html/mobile's "Check"
# button - THIS platform's own /p/<slug> address, never a real internet domain) ---

def test_slug_is_available_true_for_an_unclaimed_slug():
    assert pages.slug_is_available(f"free-{uuid.uuid4().hex[:8]}") is True


def test_slug_is_available_false_once_another_business_claims_it(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    slug = f"taken-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    assert pages.slug_is_available(slug) is False


def test_slug_is_available_true_for_the_owning_businesss_own_slug(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    slug = f"mine-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)

    assert pages.slug_is_available(slug, exclude_business_id=business_id) is True


def test_suggest_slug_alternatives_returns_free_numbered_variants(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    base = f"taken-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, base, True, None, None)

    suggestions = pages.suggest_slug_alternatives(base)
    assert len(suggestions) == 3
    assert len(set(suggestions)) == 3
    for s in suggestions:
        assert s.startswith(base)
        assert pages.is_valid_slug(s)
        assert pages.slug_is_available(s)


def test_suggest_slug_alternatives_skips_variants_already_taken(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    base = f"popular-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, base, True, None, None)
    other_email = f"other-{unique_email}"
    _, other_business_id = register_and_login(client, other_email)
    database.upsert_business_page(other_business_id, f"{base}-2", True, None, None)

    suggestions = pages.suggest_slug_alternatives(base)
    assert f"{base}-2" not in suggestions
    assert len(suggestions) == 3


def test_suggest_slug_alternatives_respects_the_fifty_char_ceiling():
    base = "a" * 50
    suggestions = pages.suggest_slug_alternatives(base)
    assert len(suggestions) == 3
    for s in suggestions:
        assert len(s) <= 50
        assert pages.is_valid_slug(s)


# --- database.py: business_pages CRUD ---

def test_get_business_page_returns_none_when_never_saved(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    assert database.get_business_page(business_id) is None
    assert database.page_by_slug(f"nonexistent-{uuid.uuid4().hex[:8]}") is None


def test_upsert_business_page_creates_then_updates(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
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


def test_upsert_business_page_clears_content_language_only_when_text_changes(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"

    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "We've been open since 2019.")
    database.set_page_content_language(business_id, "en")
    assert database.get_business_page(business_id)[16] == "en"

    # Same tagline/about, just re-saving a different field (published) - the cached
    # detection is still accurate, so it must survive.
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We've been open since 2019.")
    assert database.get_business_page(business_id)[16] == "en"

    # Rewriting the actual content invalidates the old detection - back to NULL so the
    # next page view redetects instead of staying stuck showing the old language's flag.
    database.upsert_business_page(business_id, slug, False, "Sveži rezovi svaki dan", "Radimo od 2019.")
    assert database.get_business_page(business_id)[16] is None


def test_upsert_business_page_clears_cached_translations_only_when_text_changes(client, unique_email):
    # A cached business_page_translations row is a translation of one specific snapshot
    # of tagline/about - once the owner actually rewrites either, that row is a
    # translation of text that no longer exists on the page, not a stale-but-still-
    # accurate one, so it must be cleared out rather than kept serving old wording.
    _, business_id = register_and_login(client, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"

    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "We've been open since 2019.")
    database.upsert_page_translation(business_id, "es", "Cortes frescos", "Abrimos en 2019.", "Corte: 20 EUR", "Q\nA", "{}")
    assert database.get_page_translation(business_id, "es") is not None

    # Same tagline/about, just re-saving a different field (published) - the cached
    # translation is still a translation of the CURRENT text, so it must survive.
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We've been open since 2019.")
    assert database.get_page_translation(business_id, "es") is not None

    # Rewriting the actual content invalidates every cached translation for this
    # business, not just the one language a visitor happens to pick next.
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We've been open since 2019 - now with pastries!")
    assert database.get_page_translation(business_id, "es") is None


def test_update_business_clears_cached_translations_only_when_price_list_or_faq_changes(client, unique_email):
    # Same staleness problem as tagline/about (see the upsert_business_page test above),
    # for the other two fields that feed the same translate_page_content call.
    _, business_id = register_and_login(client, unique_email)
    business = database.business_by_id(business_id)
    database.upsert_page_translation(business_id, "es", "Cortes frescos", "Abrimos en 2019.", "Corte: 20 EUR", "Q\nA", "{}")
    assert database.get_page_translation(business_id, "es") is not None

    # Same price_list/faq, just updating the name - the cached translation still matches
    # the current text, so it must survive.
    database.update_business(business_id, "New Name", business[3], business[10], business[7], business[8], business[9])
    assert database.get_page_translation(business_id, "es") is not None

    # Rewriting the price list invalidates every cached translation for this business.
    database.update_business(business_id, "New Name", business[3], business[10], business[7], "Haircut: 25 EUR", business[9])
    assert database.get_page_translation(business_id, "es") is None


def test_upsert_business_page_slug_collision_raises(client, unique_email):
    _, business_id_a = register_and_login(client, unique_email)
    other_email = f"b-{unique_email}"
    _, business_id_b = register_and_login(client, other_email)

    slug = f"shared-slug-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id_a, slug, True, None, None)

    with pytest.raises(database.UniqueViolation):
        database.upsert_business_page(business_id_b, slug, True, None, None)


def test_upsert_business_page_same_business_can_keep_its_own_slug(client, unique_email):
    """Saving the form again without changing the slug must not trip the collision check
    against the business's own existing row."""
    _, business_id = register_and_login(client, unique_email)
    slug = f"marko-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "A", "B")
    database.upsert_business_page(business_id, slug, True, "A2", "B2")  # must not raise
    assert database.get_business_page(business_id)[4] == "A2"


def test_page_by_slug_finds_it_regardless_of_published_state(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)
    found = database.page_by_slug(slug)
    assert found is not None
    assert found[1] == business_id
    assert found[3] is False  # unpublished - route decides whether that means 404


def test_published_page_slugs_only_returns_published(client, unique_email):
    _, business_id_a = register_and_login(client, unique_email)
    other_email = f"pub-{unique_email}"
    _, business_id_b = register_and_login(client, other_email)

    published_slug = f"pub-{uuid.uuid4().hex[:8]}"
    draft_slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id_a, published_slug, True, None, None)
    database.upsert_business_page(business_id_b, draft_slug, False, None, None)

    slugs = [row[0] for row in database.published_page_slugs()]
    assert published_slug in slugs
    assert draft_slug not in slugs


def test_delete_business_removes_its_page(client, unique_email):
    _, business_id = register_and_login(client, unique_email)
    slug = f"deleteme-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, None, None)
    assert database.page_by_slug(slug) is not None

    database.delete_business(business_id)
    assert database.page_by_slug(slug) is None


@pytest.mark.parametrize(
    "raw_price, expected",
    [
        ("200 dinara", "200 RSD"),
        ("200 dinara RSD", "200 RSD"),
        ("180 din", "180 RSD"),
        ("15 evra", "15 EUR"),
        ("15 EUR", "15 EUR"),
        ("200", "200"),
        ("price on request", "price on request"),
        ("", ""),
    ],
)
def test_price_list_rows_strips_currency_words_from_display(raw_price, expected):
    rows = pages.price_list_rows(f"Haircut: {raw_price}")
    assert rows == [{"type": "item", "name": "Haircut", "price": expected}]


def test_price_list_rows_leaves_no_colon_lines_untouched():
    rows = pages.price_list_rows("Consultation - price on request")
    assert rows == [{"type": "item", "name": "Consultation - price on request", "price": ""}]


def test_price_list_rows_parses_category_headers():
    rows = pages.price_list_rows("# Haircuts\nMen's: 20 EUR\nWomen's: 30 EUR\n# Add-ons\nWash: 5 EUR")
    assert rows == [
        {"type": "category", "name": "Haircuts"},
        {"type": "item", "name": "Men's", "price": "20 EUR"},
        {"type": "item", "name": "Women's", "price": "30 EUR"},
        {"type": "category", "name": "Add-ons"},
        {"type": "item", "name": "Wash", "price": "5 EUR"},
    ]


def test_price_list_rows_does_not_treat_bare_unpriced_line_as_category():
    # page_generator.format_price_list emits a colon-less line for any AI-drafted
    # service with no price given - must stay a normal item, not a category header.
    rows = pages.price_list_rows("Consultation")
    assert rows == [{"type": "item", "name": "Consultation", "price": ""}]


# --- pages.py: opening_hours_schema ---

def test_opening_hours_schema_lists_open_days_only():
    text = "Monday: 09:00-18:00\nTuesday: closed\nWednesday: 10:00-16:00"
    result = pages.opening_hours_schema(text)
    assert len(result) == 2
    assert result[0] == {
        "@type": "OpeningHoursSpecification",
        "dayOfWeek": "https://schema.org/Monday",
        "opens": "09:00",
        "closes": "18:00",
    }
    assert result[1]["dayOfWeek"] == "https://schema.org/Wednesday"
    assert result[1]["opens"] == "10:00"
    assert result[1]["closes"] == "16:00"


def test_opening_hours_schema_empty_for_fully_closed_or_unparseable():
    assert pages.opening_hours_schema("") == []
    assert pages.opening_hours_schema("garbage text") == []
    assert pages.opening_hours_schema("Monday: closed\nTuesday: closed") == []
