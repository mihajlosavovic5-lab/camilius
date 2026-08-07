"""Tests for domain_lookup.py - real-domain RDAP availability checks. requests.get is
monkeypatched, same "never make a real network call" rule the rest of the suite follows
for OpenAI calls - these never actually hit rdap.org."""
import requests

import domain_lookup


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_check_domain_returns_available_on_404(monkeypatch):
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **kw: _FakeResponse(404))
    assert domain_lookup.check_domain("free-domain-example.com") == domain_lookup.AVAILABLE


def test_check_domain_returns_taken_on_200(monkeypatch):
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **kw: _FakeResponse(200))
    assert domain_lookup.check_domain("google.com") == domain_lookup.TAKEN


def test_check_domain_returns_unknown_on_other_status(monkeypatch):
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **kw: _FakeResponse(500))
    assert domain_lookup.check_domain("some-domain.net") == domain_lookup.UNKNOWN


def test_check_domain_skips_the_network_call_for_an_unsupported_tld(monkeypatch):
    """.rs (and any TLD outside _RDAP_SUPPORTED_TLDS) has no real RDAP server - rdap.org's
    own "I don't know this TLD" 404 is indistinguishable from a real registry's "this
    domain isn't registered" 404, so this must never even make the request (which would
    let that ambiguous 404 get misread as AVAILABLE - the exact bug this guards against)."""
    def fail_if_called(*a, **kw):
        raise AssertionError("requests.get should not be called for an unsupported TLD")

    monkeypatch.setattr(domain_lookup.requests, "get", fail_if_called)
    assert domain_lookup.check_domain("mihajlo-prodaja.rs") == domain_lookup.UNKNOWN


def test_check_domain_returns_unknown_on_request_exception(monkeypatch):
    def raise_error(*a, **kw):
        raise requests.RequestException("boom")

    monkeypatch.setattr(domain_lookup.requests, "get", raise_error)
    assert domain_lookup.check_domain("some-domain.com") == domain_lookup.UNKNOWN


def test_check_domain_hits_rdap_org_with_the_full_domain(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse(404)

    monkeypatch.setattr(domain_lookup.requests, "get", fake_get)
    domain_lookup.check_domain("blue-star-cafe.com")
    assert captured["url"] == "https://rdap.org/domain/blue-star-cafe.com"
    assert captured["timeout"] == domain_lookup._RDAP_TIMEOUT
    assert captured["headers"]["User-Agent"]


def test_check_domains_builds_one_candidate_per_tld(monkeypatch):
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    results = domain_lookup.check_domains("blue-star-cafe", tlds=[".com", ".net"])
    assert results == [
        {"domain": "blue-star-cafe.com", "status": domain_lookup.AVAILABLE},
        {"domain": "blue-star-cafe.net", "status": domain_lookup.AVAILABLE},
    ]


def test_check_domains_uses_default_tlds_when_not_given(monkeypatch):
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    results = domain_lookup.check_domains("blue-star-cafe")
    assert [entry["domain"] for entry in results] == [f"blue-star-cafe{tld}" for tld in domain_lookup.DEFAULT_TLDS]


def test_check_domains_returns_empty_list_for_blank_base_name():
    assert domain_lookup.check_domains("") == []
    assert domain_lookup.check_domains(None) == []
    assert domain_lookup.check_domains("   ") == []


def test_check_domains_strips_leading_and_trailing_dots(monkeypatch):
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    results = domain_lookup.check_domains(".blue-star-cafe.", tlds=[".com"])
    assert results == [{"domain": "blue-star-cafe.com", "status": domain_lookup.AVAILABLE}]


def test_check_domains_drops_a_tld_the_owner_already_typed(monkeypatch):
    """Regression test: an owner typing "mihajlo-prodaja.com" into the web-address field
    (a very natural thing to type, even though the field itself wants a bare name) used
    to get appended straight onto each candidate TLD, producing nonsense domains like
    "mihajlo-prodaja.com.com" that RDAP could never resolve - every result came back
    UNKNOWN ("Couldn't check"). Only the part before the first "." should ever be used."""
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    results = domain_lookup.check_domains("mihajlo-prodaja.com", tlds=[".com", ".net", ".rs"])
    assert results == [
        {"domain": "mihajlo-prodaja.com", "status": domain_lookup.AVAILABLE},
        {"domain": "mihajlo-prodaja.net", "status": domain_lookup.AVAILABLE},
        {"domain": "mihajlo-prodaja.rs", "status": domain_lookup.AVAILABLE},
    ]
