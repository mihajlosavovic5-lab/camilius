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
