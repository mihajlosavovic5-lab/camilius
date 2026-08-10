"""Tests for app.py's Host-header dispatch (dispatch_custom_domain_page) - Faza 4 of
"Automatska kupovina domena" (see domain_registrar.py's own module docstring). A visitor
opening a business's own purchased domain directly should see the same page /p/<slug>
shows, only for a domain that's actually finished purchasing (custom_domain_status
'active' - see resolve_custom_domain_purchase), and the page's own chat/availability
fetch()es must still reach the platform's real domain (see public_page_chat/
public_page_availability's CORS handling) since neither of those routes is served under
the custom domain itself."""
import uuid

import app as app_module
import database
import ai_replies
from conftest import register_business_web


def _stub_ai(monkeypatch, reply):
    """Same technique as test_page_chat.py's own _stub_ai - patches both
    ai_replies-imported entry points, since which one handle_incoming_message actually
    calls depends on whether the booking tool is available, not something these CORS
    tests care about either way."""
    monkeypatch.setattr(ai_replies, "generate_reply_with_booking", lambda *a, **k: reply)
    monkeypatch.setattr(ai_replies, "generate_reply", lambda *a, **k: reply)
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)


def _publish_page_with_active_domain(client, email, domain):
    register_business_web(client, email)
    business_id = database.business_by_contact_email(email)[0]
    slug = f"page-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "Family-run since 2019.")
    database.resolve_custom_domain_purchase(business_id, domain, True)
    return business_id, slug


def _unique_domain():
    return f"salon-{uuid.uuid4().hex[:10]}.com"


def test_active_custom_domain_serves_the_business_page(client, unique_email):
    domain = _unique_domain()
    _publish_page_with_active_domain(client, unique_email, domain)

    response = client.get("/", headers={"Host": domain})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Test Salon" in html
    assert "Fresh cuts daily" in html
    assert "Family-run since 2019." in html


def test_active_custom_domain_page_url_is_the_domains_own_root(client, unique_email):
    domain = _unique_domain()
    _publish_page_with_active_domain(client, unique_email, domain)

    html = client.get("/", headers={"Host": domain}).get_data(as_text=True)
    assert f"https://{domain}/" in html


def test_active_custom_domain_chat_and_availability_urls_stay_on_the_platform(client, unique_email):
    """chat_url/availability_url must always point at PRIMARY_APP_HOST (see
    _render_public_page's own comment) - a relative URL would resolve against the
    custom domain instead and 404, since neither /p/<slug>/chat nor
    /p/<slug>/availability is ever served under a business's own custom domain."""
    domain = _unique_domain()
    business_id, slug = _publish_page_with_active_domain(client, unique_email, domain)

    html = client.get("/", headers={"Host": domain}).get_data(as_text=True)
    assert f"https://{app_module.PRIMARY_APP_HOST}/p/{slug}/chat" in html
    assert f"https://{app_module.PRIMARY_APP_HOST}/p/{slug}/availability" in html


def test_custom_domain_not_yet_active_is_not_served(client, unique_email):
    """custom_domain can be set (a purchase in flight, or one that failed after
    resolve_custom_domain_purchase(success=False) already cleared it - either way this
    guards the WHERE clause itself, not just how today's helpers happen to call it) -
    only 'active' should ever be servable."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"page-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)
    domain = _unique_domain()
    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE business_pages SET custom_domain = %s, custom_domain_status = 'purchasing' WHERE business_id = %s",
        (domain, business_id),
    )
    connection.commit()
    connection.close()

    response = client.get("/", headers={"Host": domain})
    assert "Fresh cuts daily" not in response.get_data(as_text=True)


def test_unrelated_host_is_unaffected(client):
    response = client.get("/", headers={"Host": f"not-a-registered-domain-{uuid.uuid4().hex[:8]}.com"})
    assert response.status_code == 200
    assert "Fresh cuts daily" not in response.get_data(as_text=True)


def test_active_custom_domain_only_dispatches_the_root_path(client, unique_email):
    """Scoped to "/" only (see dispatch_custom_domain_page's own docstring) - a request
    for any other path on the custom domain's Host falls through to normal routing
    rather than being silently swallowed."""
    domain = _unique_domain()
    _publish_page_with_active_domain(client, unique_email, domain)

    response = client.get("/some-other-path", headers={"Host": domain})
    assert response.status_code == 404


def test_page_chat_allows_cors_for_the_business_own_custom_domain(client, unique_email, monkeypatch):
    domain = _unique_domain()
    business_id, slug = _publish_page_with_active_domain(client, unique_email, domain)
    _stub_ai(monkeypatch, "We're open 9-6!")

    response = client.post(
        f"/p/{slug}/chat",
        json={"message": "Are you open?", "session_id": f"web:{uuid.uuid4()}"},
        headers={"Origin": f"https://{domain}"},
    )
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == f"https://{domain}"
    # Flask itself appends "Cookie" to Vary here (a session cookie touched during the
    # request) - just confirm "Origin" made it in alongside whatever else did.
    assert "Origin" in (response.headers.get("Vary") or "").split(", ")


def test_page_chat_omits_cors_header_for_an_unrelated_origin(client, unique_email, monkeypatch):
    domain = _unique_domain()
    business_id, slug = _publish_page_with_active_domain(client, unique_email, domain)
    _stub_ai(monkeypatch, "We're open 9-6!")

    response = client.post(
        f"/p/{slug}/chat",
        json={"message": "Are you open?", "session_id": f"web:{uuid.uuid4()}"},
        headers={"Origin": "https://some-other-site.com"},
    )
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_page_chat_omits_cors_header_with_no_custom_domain_at_all(client, unique_email, monkeypatch):
    """The overwhelmingly common case - a business with no custom domain purchased yet,
    hit the normal way (from /p/<slug> itself, same-origin, no Origin header at all in
    practice - but even one that showed up should never get a CORS allowance since
    there's no registered domain to match it against)."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"page-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)
    _stub_ai(monkeypatch, "We're open 9-6!")

    response = client.post(
        f"/p/{slug}/chat",
        json={"message": "Are you open?", "session_id": f"web:{uuid.uuid4()}"},
        headers={"Origin": "https://anything.com"},
    )
    assert "Access-Control-Allow-Origin" not in response.headers


def test_page_chat_preflight_allows_the_business_own_custom_domain(client, unique_email):
    domain = _unique_domain()
    business_id, slug = _publish_page_with_active_domain(client, unique_email, domain)

    response = client.options(f"/p/{slug}/chat", headers={"Origin": f"https://{domain}"})
    assert response.status_code == 204
    assert response.headers.get("Access-Control-Allow-Origin") == f"https://{domain}"
    assert response.headers.get("Access-Control-Allow-Methods") == "POST"
    assert "Content-Type" in response.headers.get("Access-Control-Allow-Headers", "")


def test_availability_allows_cors_for_the_business_own_custom_domain(client, unique_email):
    domain = _unique_domain()
    business_id, slug = _publish_page_with_active_domain(client, unique_email, domain)

    response = client.get(f"/p/{slug}/availability", headers={"Origin": f"https://{domain}"})
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == f"https://{domain}"
