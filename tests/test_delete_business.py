import database
from conftest import register_business


def test_delete_business_cleans_up_across_all_fk_tables(client, unique_username, unique_email):
    """Regression test: delete_business() used to only clear the conversations table
    before deleting the businesses row, but processed_messages/analytics_events/
    push_tokens/client_errors all also have a business_id foreign key - deleting hit a
    foreign-key violation (500) for any business with so much as one logged analytics
    event, which registration itself always logs. Found while manually testing the
    native mobile registration flow against production; fixed in database.py."""
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    # register_completed already logged one analytics_events row for this business,
    # which is exactly what used to make this raise.
    database.delete_business(business_id)

    assert database.business_by_id(business_id) is None
