"""Regression test for a real pivot-breaking bug found while working on the mobile app:
home() (the "/" dashboard route) used to hard-redirect every logged-in business to
/connect-instagram whenever instagram_account_id wasn't set ("can't do anything without
it" - a comment that predates this pivot and was never updated). Since F1-F5, that's
false: a business can fully operate via its hosted page/chat widget without ever
touching Instagram, so this gate silently made the entire pivot unreachable through
normal navigation (login -> home() -> bounced to connect-instagram, no way to reach
Profile/Schedule/Settings) for exactly the accounts the pivot is meant to serve."""
import uuid

import database
from conftest import register_business_web


def test_dashboard_reachable_without_instagram_connected(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    assert database.business_by_id(business_id)[5] is None  # instagram_account_id - never connected

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Camilius is watching your inbox" in html  # dashboard's own empty state, not a redirect


def test_dashboard_still_reachable_once_instagram_is_connected(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    database.link_instagram_account(business_id, f"ig-{uuid.uuid4().hex[:10]}", "the_biz", "fake-token")

    response = client.get("/")
    assert response.status_code == 200
