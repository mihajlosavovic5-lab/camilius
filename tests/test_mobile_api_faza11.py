"""Tests for the mobile /api/* additions that give the native app parity with the
website's Faza 8-10 features: pro_access on the dashboard payload, the reminders toggle
and upsell pairs field (both Pro-gated server-side, not just hidden in the UI), and the
Schedule bridge link."""
import app as app_module
import database
from conftest import register_and_login


def _auth_header(client, username, email, **overrides):
    token, business_id = register_and_login(client, username, email, **overrides)
    return {"Authorization": f"Bearer {token}"}, business_id


def test_dashboard_reports_pro_access_during_trial(client, unique_username, unique_email):
    headers, _ = _auth_header(client, unique_username, unique_email)
    response = client.get("/api/dashboard", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["pro_access"] is True  # fresh account is trialing


def test_dashboard_reports_no_pro_access_on_starter_plan(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_username, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")
    response = client.get("/api/dashboard", headers=headers)
    assert response.get_json()["pro_access"] is False


def test_notification_preferences_includes_reminders_enabled(client, unique_username, unique_email):
    headers, _ = _auth_header(client, unique_username, unique_email)
    get_response = client.get("/api/notification-preferences", headers=headers)
    assert get_response.get_json()["reminders_enabled"] is False

    post_response = client.post("/api/notification-preferences", headers=headers, json={"reminders_enabled": True})
    assert post_response.get_json()["reminders_enabled"] is True  # trialing -> pro_access -> allowed


def test_notification_preferences_reminders_ignored_on_starter_plan(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_username, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.post("/api/notification-preferences", headers=headers, json={"reminders_enabled": True})
    # Silently no-oped, not an error - same "the UI already hides this" spirit as the website.
    assert response.status_code == 200
    assert response.get_json()["reminders_enabled"] is False
    assert database.is_reminders_enabled(business_id) is False


def test_notification_preferences_new_booking_toggle_not_pro_gated(client, unique_username, unique_email, monkeypatch):
    # Unlike reminders_enabled/owner_reminders_enabled, new_booking_notifications_enabled
    # is a base feature (same as push_enabled/auto_send) - a Starter-plan business can
    # still flip it, no Pro/BETA_FREE_MODE check involved.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_username, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    get_response = client.get("/api/notification-preferences", headers=headers)
    assert get_response.get_json()["new_booking_notifications_enabled"] is True

    post_response = client.post("/api/notification-preferences", headers=headers, json={"new_booking_notifications_enabled": False})
    assert post_response.get_json()["new_booking_notifications_enabled"] is False
    assert database.is_new_booking_notifications_enabled(business_id) is False


def test_api_notifications_list_and_mark_read(client, unique_username, unique_email):
    # Owner appointment reminders (reminders.py) land in this table instead of an email -
    # this is the mobile app's own bell/notifications screen reading it back.
    headers, business_id = _auth_header(client, unique_username, unique_email)
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


def test_settings_includes_upsell_pairs(client, unique_username, unique_email):
    headers, _ = _auth_header(client, unique_username, unique_email)
    get_response = client.get("/api/settings", headers=headers)
    assert get_response.get_json()["upsell_pairs"] == ""

    post_response = client.post("/api/settings", headers=headers, json={"upsell_pairs": "Haircut -> Treatment"})
    assert post_response.status_code == 200
    assert post_response.get_json()["upsell_pairs"] == "Haircut -> Treatment"


def test_settings_upsell_pairs_ignored_on_starter_plan(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_username, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.post("/api/settings", headers=headers, json={"upsell_pairs": "Haircut -> Treatment"})
    assert response.status_code == 200
    assert database.get_upsell_pairs(business_id) is None


def test_schedule_link_requires_auth(client):
    response = client.get("/api/schedule-link")
    assert response.status_code == 401


def test_schedule_link_bridges_to_a_logged_in_schedule_page(client, unique_username, unique_email):
    headers, _ = _auth_header(client, unique_username, unique_email)
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
