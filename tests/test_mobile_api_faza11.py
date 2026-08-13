"""Tests for the mobile /api/* additions that give the native app parity with the
website's Faza 8-10 features: pro_access on the dashboard payload, the reminders toggle
and upsell pairs field (both Pro-gated server-side, not just hidden in the UI), and the
Schedule bridge link."""
import app as app_module
import database
from conftest import register_and_login


def _auth_header(client, email, **overrides):
    token, business_id = register_and_login(client, email, **overrides)
    return {"Authorization": f"Bearer {token}"}, business_id


def test_dashboard_reports_pro_access_during_trial(client, unique_email):
    headers, _ = _auth_header(client, unique_email)
    response = client.get("/api/dashboard", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["pro_access"] is True  # fresh account is trialing


def test_dashboard_reports_no_pro_access_on_starter_plan(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")
    response = client.get("/api/dashboard", headers=headers)
    assert response.get_json()["pro_access"] is False


def test_notification_preferences_owner_reminders_pro_gated(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.post("/api/notification-preferences", headers=headers, json={"owner_reminders_enabled": True})
    # Silently no-oped, not an error - same "the UI already hides this" spirit as the website.
    assert response.status_code == 200
    assert response.get_json()["owner_reminders_enabled"] is False
    assert database.is_owner_reminders_enabled(business_id) is False


def test_notification_preferences_new_booking_toggle_not_pro_gated(client, unique_email, monkeypatch):
    # Unlike owner_reminders_enabled, new_booking_notifications_enabled is a base feature
    # (same as push_enabled) - a Starter-plan business can still flip it, no Pro/
    # BETA_FREE_MODE check involved.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    get_response = client.get("/api/notification-preferences", headers=headers)
    assert get_response.get_json()["new_booking_notifications_enabled"] is True

    post_response = client.post("/api/notification-preferences", headers=headers, json={"new_booking_notifications_enabled": False})
    assert post_response.get_json()["new_booking_notifications_enabled"] is False
    assert database.is_new_booking_notifications_enabled(business_id) is False


def test_notification_preferences_ai_task_toggle_not_pro_gated(client, unique_email, monkeypatch):
    # "AI replies" (the "Draft this with AI" chat's own push, see api._notify_website_chat_done)
    # is a base feature too, on by default - same shape as new_booking_notifications_enabled above.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    get_response = client.get("/api/notification-preferences", headers=headers)
    assert get_response.get_json()["ai_task_notifications_enabled"] is True

    post_response = client.post("/api/notification-preferences", headers=headers, json={"ai_task_notifications_enabled": False})
    assert post_response.get_json()["ai_task_notifications_enabled"] is False
    assert database.is_ai_task_notifications_enabled(business_id) is False


def test_api_notifications_list_and_mark_read(client, unique_email):
    # Owner appointment reminders (reminders.py) land in this table instead of an email -
    # this is the mobile app's own bell/notifications screen reading it back.
    headers, business_id = _auth_header(client, unique_email)
    database.create_notification(business_id, "Upcoming appointment reminder", "You have an appointment with Ana in 1 hour.")

    get_response = client.get("/api/notifications", headers=headers)
    assert get_response.status_code == 200
    data = get_response.get_json()
    assert data["unread_count"] == 1
    assert len(data["notifications"]) == 1
    assert data["notifications"][0]["read"] is False

    post_response = client.post("/api/notifications/read", headers=headers, json={})
    assert post_response.get_json()["unread_count"] == 0
    assert client.get("/api/notifications", headers=headers).get_json()["unread_count"] == 0


def test_settings_includes_upsell_pairs(client, unique_email):
    headers, _ = _auth_header(client, unique_email)
    get_response = client.get("/api/settings", headers=headers)
    assert get_response.get_json()["upsell_pairs"] == ""

    post_response = client.post("/api/settings", headers=headers, json={"upsell_pairs": "Haircut -> Treatment"})
    assert post_response.status_code == 200
    assert post_response.get_json()["upsell_pairs"] == "Haircut -> Treatment"


def test_settings_upsell_pairs_ignored_on_starter_plan(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.post("/api/settings", headers=headers, json={"upsell_pairs": "Haircut -> Treatment"})
    assert response.status_code == 200
    assert database.get_upsell_pairs(business_id) is None


def test_schedule_link_requires_auth(client):
    response = client.get("/api/schedule-link")
    assert response.status_code == 401


def test_schedule_link_bridges_to_a_logged_in_schedule_page(client, unique_email):
    headers, _ = _auth_header(client, unique_email)
    link_response = client.get("/api/schedule-link", headers=headers)
    assert link_response.status_code == 200
    url = link_response.get_json()["url"]
    assert "/schedule/" in url

    token = url.rsplit("/schedule/", 1)[1]
    # Following the bridge should establish a session (see the fix in app.py's
    # schedule_mobile) and land on the real, unlocked Schedule page - not a login wall.
    bridge_response = client.get(f"/schedule/{token}", follow_redirects=True)
    assert bridge_response.status_code == 200
    assert "Schedule is a Pro plan feature" not in bridge_response.get_data(as_text=True)


def test_schedule_link_rejects_bad_token(client):
    response = client.get("/schedule/not-a-real-token")
    assert response.status_code == 404


def test_site_preview_link_requires_auth(client):
    response = client.get("/api/site-preview-link")
    assert response.status_code == 401


def test_site_preview_link_bridges_to_a_logged_in_preview(client, unique_email):
    headers, business_id = _auth_header(client, unique_email)
    database.upsert_business_page(business_id, f"draft-{business_id}", False, "Fresh cuts daily", None)

    link_response = client.get("/api/site-preview-link", headers=headers)
    assert link_response.status_code == 200
    url = link_response.get_json()["url"]
    assert "/site/preview/" in url

    token = url.rsplit("/site/preview/", 1)[1]
    # Same session-bridge fix as the Schedule link above (see app.py's
    # site_preview_mobile) - lands on the real preview, unpublished page included,
    # rather than a login wall.
    bridge_response = client.get(f"/site/preview/{token}", follow_redirects=True)
    assert bridge_response.status_code == 200
    assert "Fresh cuts daily" in bridge_response.get_data(as_text=True)


def test_site_preview_link_rejects_bad_token(client):
    response = client.get("/site/preview/not-a-real-token")
    assert response.status_code == 404


def test_site_preview_link_live_redirects_to_public_page_when_published(client, unique_email):
    """The mobile app's "View page" button opens the real /p/<slug> URL once a page is
    published (openLiveSite in mobile/app.js), appending ?live=1 to this same bridge
    link instead of opening /p/<slug> directly with no session - see app.py's
    site_preview_mobile. Without a real session there, the owner's own theme picker
    (public_page.html's is_owner-gated toolbar) could never tell the owner apart from
    any other visitor on that exact URL."""
    headers, business_id = _auth_header(client, unique_email)
    slug = f"live-{business_id}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)

    link_response = client.get("/api/site-preview-link", headers=headers)
    token = link_response.get_json()["url"].rsplit("/site/preview/", 1)[1]

    bridge_response = client.get(f"/site/preview/{token}?live=1", follow_redirects=True)
    assert bridge_response.status_code == 200
    html = bridge_response.get_data(as_text=True)
    assert "Fresh cuts daily" in html
    assert 'id="theme-fab"' in html


def test_site_preview_link_live_falls_back_to_preview_when_unpublished(client, unique_email):
    headers, business_id = _auth_header(client, unique_email)
    slug = f"draft-{business_id}"
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", None)

    link_response = client.get("/api/site-preview-link", headers=headers)
    token = link_response.get_json()["url"].rsplit("/site/preview/", 1)[1]

    bridge_response = client.get(f"/site/preview/{token}?live=1")
    assert bridge_response.status_code == 302
    assert bridge_response.headers["Location"] == "/site/preview"
