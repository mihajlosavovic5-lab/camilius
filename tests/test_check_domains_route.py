"""Route tests for POST /profile/check-domains - domain_lookup.py's RDAP-based real-
domain availability check exposed to settings.html's "Check real domain availability"
button. domain_lookup.check_domain is monkeypatched, same "never make a real network
call" rule as the rest of the suite."""
import database
import domain_lookup
from conftest import register_business_web, web_post, get_csrf_token


def test_check_domains_returns_results_for_each_default_tld(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-domains", {"csrf_token": csrf_token, "name": "blue-star-cafe"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["domains"] == [{"domain": f"blue-star-cafe{tld}", "status": domain_lookup.AVAILABLE} for tld in domain_lookup.DEFAULT_TLDS]


def test_check_domains_requires_a_name(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/check-domains", {"csrf_token": csrf_token, "name": "   "})
    assert response.status_code == 400


def test_check_domains_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = web_post(client, "/profile/check-domains", {"csrf_token": csrf_token, "name": "blue-star-cafe"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_check_domains_never_writes_to_the_database(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.TAKEN)
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    csrf_token = get_csrf_token(client, "/profile")

    web_post(client, "/profile/check-domains", {"csrf_token": csrf_token, "name": "blue-star-cafe"})
    assert database.get_business_page(business_id) is None
