"""Background worker loop - kept as its own long-running process (started alongside
gunicorn, see .railway/railway.ts's "start" command) because Railway only lets a data
volume attach to one service, and this and the web process share the same database.

Filename kept as process_emails.py for historical reasons (renaming it would mean
changing railway.ts's start command, which needs `railway config apply` to actually take
effect on the live service, and the Railway CLI is currently blocked on this machine -
see deploy-railway-state memory. Not worth the deployment risk for a cosmetic rename.

What this loop actually does now:
- Daily database backup (unchanged from before).
- Daily trash purge (30+ day old soft-deleted conversations).
- Appointment reminders (Faza 9) - checked every tick, not once/day like the jobs above,
  since a 24h/2h-before reminder needs much tighter timing. See reminders.py.
- Trial-ending warning emails - checked every tick too; cheap (DB-side dedup via
  trial_warning_sent_at means a business is only ever matched once), and Paddle Billing
  has no webhook to react to instead of polling. See reminders.py.
- Auto-archiving conversations stuck 'pending' past 24h - checked every tick (cheap, a
  single UPDATE) so nothing sits in the main Pending list long after it's stale.
  See reminders.py.
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import time
from datetime import datetime, date

from database import create_database, purge_expired_trash
from backup import daily_backup_if_needed
from reminders import (
    send_appointment_reminders_if_needed, send_trial_ending_warnings_if_needed,
    expire_stale_pending_conversations_if_needed,
)

# Tightened from 3600s (hourly) to 300s (5 min) for Faza 9's appointment reminders,
# which need much finer timing than the once-a-day jobs below - those stay correct
# either way since each is separately self-gated to run at most once per calendar day
# via its own file marker, so checking them more often than that is simply a no-op.
INTERVAL_SECONDS = 300


def purge_expired_trash_if_needed():
    """Once per day: permanently delete anything that's sat in the Trash for 30+ days
    (Faza 3). Same "already ran today" file-marker approach as the other daily jobs above."""
    import os
    from backup import BACKUP_DIR

    if not BACKUP_DIR:
        return

    marker = os.path.join(BACKUP_DIR, f"trash-purge-{date.today().isoformat()}.done")
    if os.path.exists(marker):
        return

    purged = purge_expired_trash()

    os.makedirs(BACKUP_DIR, exist_ok=True)
    open(marker, "w").close()
    print(f"[trash-purge] Daily purge complete - removed {purged} conversation(s).")


def run_loop():
    create_database()
    print(f"Started - background worker checking in every {INTERVAL_SECONDS} seconds. (Ctrl+C to stop)")

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            daily_backup_if_needed()
            purge_expired_trash_if_needed()
            send_appointment_reminders_if_needed()
            send_trial_ending_warnings_if_needed()
            expire_stale_pending_conversations_if_needed()
        except Exception as error:
            # Don't let one error (e.g. a temporary network blip) crash the whole loop
            print(f"[{now}] An error occurred: {error}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run_loop()
