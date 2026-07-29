"""Web dashboard's bell icon (_nav.html) reads these two session-authed routes - the
mobile app's Bearer-authed /api/notifications equivalent is covered in
test_mobile_api_faza11.py. Both sit on top of the same database.notifications table."""
import database
from conftest import get_csrf_token, register_business_web, web_post


def test_web_notifications_list_and_mark_read(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    database.create_notification(business_id, "Upcoming appointment reminder", "You have an appointment with Ana in 1 hour.")

    get_response = client.get("/notifications")
    assert get_response.status_code == 200
    data = get_response.get_json()
    assert data["unread_count"] == 1
    assert len(data["notifications"]) == 1
    assert data["notifications"][0]["read"] is False

    csrf_token = get_csrf_token(client, "/settings")
    post_response = client.post(
        "/notifications/read",
        json={},
        headers={"X-CSRFToken": csrf_token, "Referer": "http://localhost/settings"},
    )
    assert post_response.status_code == 200
    assert post_response.get_json()["unread_count"] == 0
    assert client.get("/notifications").get_json()["unread_count"] == 0


def test_web_notifications_requires_login(client):
    response = client.get("/notifications")
    assert response.status_code in (302, 401)


def test_web_settings_new_booking_toggle(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    assert database.is_new_booking_notifications_enabled(business_id) is True  # column default

    csrf_token = get_csrf_token(client, "/settings")
    response = web_post(client, "/settings/notifications", {"csrf_token": csrf_token})  # checkbox unchecked
    assert response.status_code == 302
    assert database.is_new_booking_notifications_enabled(business_id) is False

    csrf_token = get_csrf_token(client, "/settings")
    web_post(client, "/settings/notifications", {"csrf_token": csrf_token, "new_booking_notifications_enabled": "on"})
    assert database.is_new_booking_notifications_enabled(business_id) is True
