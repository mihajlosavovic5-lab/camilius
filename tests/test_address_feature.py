"""Sign-up's own optional address field (businesses.address) and the address-suggestion
endpoint that backs its and every other address field's autocomplete dropdown
(address_lookup.py, via OpenStreetMap Nominatim - no API key, unlike Google Places).

businesses.address is only ever a starting point for business_pages.address (the one
actually shown on the public page's Location section, see database.py's add_business
docstring) - settings.html falls back to it only while no page-level address has been
set yet, never overriding one that already exists.
"""
import uuid

import address_lookup
import database
from conftest import register_business_web


def test_register_web_saves_the_address_field(client):
    email = f"addr-{uuid.uuid4().hex[:8]}@example.com"
    register_business_web(client, email, address="Kneza Miloša 12, Kragujevac, Srbija")

    business = database.business_by_contact_email(email)
    assert business[30] == "Kneza Miloša 12, Kragujevac, Srbija"


def test_register_web_leaves_address_null_when_left_blank(client):
    email = f"addr-{uuid.uuid4().hex[:8]}@example.com"
    register_business_web(client, email, address="")

    business = database.business_by_contact_email(email)
    assert business[30] is None


def test_register_mobile_saves_the_address_field(client):
    email = f"addr-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post("/api/register", json={
        "name": "Mobile Biz", "password": "password123", "contact_email": email,
        "working_hours": "Mon: 9-17", "price_list": "Cut: 10", "accept_terms": True,
        "address": "Mobile Signup Address 1",
    })

    assert response.status_code == 200
    business = database.business_by_contact_email(email)
    assert business[30] == "Mobile Signup Address 1"


def test_settings_falls_back_to_the_signup_address_before_a_page_exists(client):
    email = f"addr-{uuid.uuid4().hex[:8]}@example.com"
    register_business_web(client, email, address="Some Street 5, Belgrade")

    response = client.get("/profile")

    assert "Some Street 5, Belgrade" in response.get_data(as_text=True)


def test_settings_prefers_the_page_address_once_one_is_set(client):
    # The page's own address (business_pages.address, editable independently) always
    # wins over the sign-up-time seed once a page actually exists - the seed is a
    # starting point, not something that keeps overriding a later edit.
    email = f"addr-{uuid.uuid4().hex[:8]}@example.com"
    register_business_web(client, email, address="Signup Street 1")
    business = database.business_by_contact_email(email)
    database.upsert_business_page(business[0], f"addr-{uuid.uuid4().hex[:6]}", True, "Tag", "About", address="Real Page Address 99")

    html = client.get("/profile").get_data(as_text=True)

    assert "Real Page Address 99" in html
    assert "Signup Street 1" not in html


def test_address_suggest_endpoint_returns_suggestions_from_address_lookup(client, monkeypatch):
    monkeypatch.setattr(address_lookup, "suggest_addresses", lambda q: ["Fake Address 1", "Fake Address 2"])

    response = client.get("/api/address-suggest?q=test")

    assert response.status_code == 200
    assert response.get_json()["suggestions"] == ["Fake Address 1", "Fake Address 2"]


def test_address_suggest_endpoint_is_unauthenticated(client, monkeypatch):
    # Must work with no Bearer token/session - it's called from the Sign up screen,
    # before any account exists.
    monkeypatch.setattr(address_lookup, "suggest_addresses", lambda q: [])
    response = client.get("/api/address-suggest?q=anything")
    assert response.status_code == 200


def test_suggest_addresses_skips_the_network_call_for_a_short_query():
    assert address_lookup.suggest_addresses("ab") == []


def test_suggest_addresses_returns_empty_list_on_request_failure(monkeypatch):
    import requests

    def fake_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(address_lookup.requests, "get", fake_get)
    assert address_lookup.suggest_addresses("a real query") == []
