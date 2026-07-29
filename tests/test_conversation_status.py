import database
from conftest import register_business


def test_new_conversation_defaults_to_pending_status(client, unique_username, unique_email):
    """Regression test: the conversations.status column's DEFAULT constraint was still
    the pre-migration literal 'na_cekanju' (Serbian) on the live production table -
    CREATE TABLE IF NOT EXISTS never applies to an already-existing table, so the
    2026-07-16 rename to English only ever updated the code, never the column default.
    Every conversation created since then silently got status='na_cekanju', which
    matched neither 'pending' nor 'sent' anywhere in app.py/dashboard.html - so it fell
    into the "else" (sent) branch with an empty final_reply, showing the business owner
    a reply of literally "None" for a message that was never actually sent. Fixed in
    create_database() (ALTER COLUMN status SET DEFAULT 'pending' + a one-time UPDATE for
    already-stuck rows)."""
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    conversation_id = database.save_draft_reply(
        business_id, "test_sender", "How much for a haircut?", "It's 800 dinars.",
    )

    conversation = database.get_conversation(conversation_id)
    # id, business_id, sender, subject, question, suggested_reply, sender_igsid
    assert conversation[5] == "It's 800 dinars."

    conversations = database.conversations_for_business(business_id)
    # id, sender, subject, question, suggested_reply, final_reply, status, created_at
    assert conversations[0][6] == "pending"


def test_expire_stale_pending_conversations_archives_only_conversations_past_24h(client, unique_username, unique_email):
    """Instagram rejects a reply sent more than 24h after the customer's own last
    message no matter what (see instagram_api.send_message) - a conversation still
    'pending' past that point can never actually be replied to, so it should get swept
    out of the main Inbox automatically rather than sitting there forever. Checks both
    sides: a conversation older than 24h gets archived_at/expired_at set and disappears
    from conversations_for_business, while one still inside the window is untouched."""
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    stale_id = database.save_draft_reply(
        business_id, "stale_sender", "How much for a haircut?", "It's 800 dinars.",
    )
    fresh_id = database.save_draft_reply(
        business_id, "fresh_sender", "Are you open Sunday?", "Yes, 10am-6pm.",
    )

    connection = database.connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE conversations SET created_at = created_at - INTERVAL '25 hours' WHERE id = %s", (stale_id,))
    connection.commit()
    connection.close()

    expired_count = database.expire_stale_pending_conversations()
    assert expired_count == 1

    remaining_ids = [c[0] for c in database.conversations_for_business(business_id)]
    assert stale_id not in remaining_ids
    assert fresh_id in remaining_ids

    archived = {c[0]: c for c in database.archived_conversations_for_business(business_id)}
    assert stale_id in archived
    # status, expired_at are indices 6 and 9 (see archived_conversations_for_business's SELECT)
    assert archived[stale_id][6] == "pending"
    assert archived[stale_id][9] is not None
