import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
from datetime import datetime, date

from database import connect

# Folder where daily backup files are written (on Railway: /data/backups,
# on the existing persistent volume that survives redeploys).
# If BACKUP_DIR isn't set, backups are skipped.
BACKUP_DIR = os.getenv("BACKUP_DIR")

# How many recent daily backups to keep before deleting the oldest
BACKUP_COUNT = 14


def create_backup():
    """Writes the full contents of the database (both tables) to a JSON file. Returns the file path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    connection = connect()
    cursor = connection.cursor()

    data = {"created_at": datetime.now().isoformat()}

    cursor.execute("SELECT id, name, username, password, email, email_app_password, working_hours, price_list, faq FROM businesses ORDER BY id")
    data["businesses"] = cursor.fetchall()

    cursor.execute("SELECT id, business_id, sender, subject, question, suggested_reply, final_reply, status, created_at::text FROM conversations ORDER BY id")
    data["conversations"] = cursor.fetchall()

    connection.close()

    path = os.path.join(BACKUP_DIR, f"backup-{date.today().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    delete_old_backups()
    return path


def delete_old_backups():
    files = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("backup-") and f.endswith(".json")
    )
    for extra in files[:-BACKUP_COUNT]:
        os.remove(os.path.join(BACKUP_DIR, extra))


def daily_backup_if_needed():
    """Called from the worker loop - creates a backup at most once a day.

    Today's file already exists -> does nothing. BACKUP_DIR not set -> skips.
    """
    if not BACKUP_DIR:
        return

    today_file = os.path.join(BACKUP_DIR, f"backup-{date.today().isoformat()}.json")
    if os.path.exists(today_file):
        return

    path = create_backup()
    print(f"[backup] Daily backup created: {path}")


if __name__ == "__main__":
    if not BACKUP_DIR:
        print("BACKUP_DIR isn't set - set it and run again.")
        sys.exit(1)
    print(f"Backup created: {create_backup()}")
