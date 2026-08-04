"""Regression test for a real pivot-breaking bug found while working on the mobile app:
home() (the "/" dashboard route) used to hard-redirect every logged-in business to
/connect-instagram whenever instagram_account_id wasn't set ("can't do anything without
it" - a comment that predates this pivot and was never updated). Since F1-F5 (and the
later removal of Instagram/WhatsApp/Messenger entirely), that's false: a business fully
operates via its hosted page/chat widget without any external channel connected, so this
gate silently made the entire pivot unreachable through normal navigation (login ->
home() -> bounced to connect-instagram, no way to reach Profile/Schedule/Settings)."""
from conftest import register_business_web


def test_dashboard_reachable_for_a_fresh_business(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Camilius is ready to chat with your visitors" in html  # dashboard's own empty state, not a redirect
