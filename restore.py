import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import argparse

from database import connect


def restore_from_backup(path):
    """Overwrites the ENTIRE businesses and conversations tables with the contents of a
    JSON backup file (produced by backup.py). IDs are restored exactly as they were in
    the backup so foreign keys (conversations.business_id) stay valid."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    connection = connect()
    cursor = connection.cursor()

    # Conversations first (foreign key to the business)
    cursor.execute("DELETE FROM conversations")
    cursor.execute("DELETE FROM businesses")

    for row in data["businesses"]:
        cursor.execute(
            """INSERT INTO businesses
               (id, name, username, password, email, email_app_password, working_hours, price_list, faq)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            tuple(row),
        )

    for row in data["conversations"]:
        cursor.execute(
            """INSERT INTO conversations
               (id, business_id, sender, subject, question, suggested_reply, final_reply, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            tuple(row),
        )

    # SERIAL sequences must be advanced past the restored IDs, otherwise the next
    # new registration/conversation could try to reuse an already-taken ID
    cursor.execute("SELECT setval(pg_get_serial_sequence('businesses', 'id'), COALESCE((SELECT MAX(id) FROM businesses), 1))")
    cursor.execute("SELECT setval(pg_get_serial_sequence('conversations', 'id'), COALESCE((SELECT MAX(id) FROM conversations), 1))")

    connection.commit()
    connection.close()

    return len(data["businesses"]), len(data["conversations"]), data.get("created_at")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restores the database from a JSON backup file. DELETES all current data!")
    parser.add_argument("path", help="Path to the backup-YYYY-MM-DD.json file")
    parser.add_argument("--yes", action="store_true", help="Confirm you really want to overwrite the current database")
    args = parser.parse_args()

    if not args.yes:
        print("This DELETES all current data in the database and restores it from the backup.")
        print("If you're sure, run again with --yes")
        sys.exit(1)

    business_count, conversation_count, created_at = restore_from_backup(args.path)
    print(f"Restored from backup (created {created_at}): {business_count} businesses, {conversation_count} conversations.")
