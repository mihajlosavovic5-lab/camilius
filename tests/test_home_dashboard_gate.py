"""Regression test for a real pivot-breaking bug found while working on the mobile app:
home() (the "/" dashboard route) used to hard-redirect every logged-in business to
/connect-instagram whenever instagram_account_id wasn't set ("can't do anything without
it" - a comment that predates this pivot and was never updated). Since F1-F5 (and the
later removal of Instagram/WhatsApp/Messenger entirely), that's false: a business fully
operates via its hosted page/chat widget without any external channel connected, so this
gate silently made the entire pivot unreachable through normal navigation (login ->
home() -> bounced to connect-instagram, no way to reach Profile/Schedule/Settings).

Faza 13 added a real (intentional) redirect back onto home() - the mandatory "build your
website" wizard for any business that hasn't finished it yet (onboarding_required in
app.py). The two tests below cover both sides of that: a fresh business is sent to the
wizard, not a dead end, and once the wizard is done home() reaches the real dashboard."""
import database
from conftest import register_business_web


def test_fresh_business_is_sent_to_the_onboarding_wizard(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)

    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/website"


def test_dashboard_reachable_once_onboarding_is_done(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Camilius is ready to chat with your visitors" in html  # dashboard's own empty state, not a redirect
