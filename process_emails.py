"""Background worker loop - kept as its own long-running process (started alongside
gunicorn, see .railway/railway.ts's "start" command) because Railway only lets a data
volume attach to one service, and this and the web process share the same database.

Filename kept as process_emails.py even though it no longer processes emails (Instagram
DMs arrive via webhook now, handled directly in app.py's /instagram-webhook route, not
by polling here) - renaming it would mean changing railway.ts's start command, which
needs `railway config apply` to actually take effect on the live service, and the
Railway CLI is currently blocked on this machine (see deploy-railway-state memory). Not
worth the deployment risk for a cosmetic rename.

What this loop actually does now:
- Daily database backup (unchanged from before).
- Daily Instagram access token refresh for every connected business - long-lived tokens
  last 60 days but can (and should) be refreshed well before that; refreshing daily is
  simple and safe (Meta's own refresh endpoint just no-ops/errors if it's too soon,
  which we never hit since we only try once a day).
- Daily trash purge (30+ day old soft-deleted conversations).
- Appointment reminders (Faza 9) - checked every tick, not once/day like the jobs above,
  since a 24h/2h-before reminder needs much tighter timing. See reminders.py.
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import time
from datetime import datetime, date

from database import create_database, all_businesses, link_instagram_account, purge_expired_trash
from backup import daily_backup_if_needed
from instagram_api import refresh_long_lived_token
from reminders import send_appointment_reminders_if_needed

# Tightened from 3600s (hourly) to 300s (5 min) for Faza 9's appointment reminders,
# which need much finer timing than the once-a-day jobs below - those stay correct
# either way since each is separately self-gated to run at most once per calendar day
# via its own file marker, so checking them more often than that is simply a no-op.
INTERVAL_SECONDS = 300


def refresh_instagram_tokens_if_needed():
    """Once per day: refresh every connected business's Instagram access token. Reuses
    the same "already ran today" file-marker approach as backup.py, keyed separately."""
    import os
    from backup import BACKUP_DIR

    if not BACKUP_DIR:
        return  # no persistent volume configured to store the day-marker in

    marker = os.path.join(BACKUP_DIR, f"instagram-token-refresh-{date.today().isoformat()}.done")
    if os.path.exists(marker):
        return

    for business in all_businesses():
        business_id, name = business[0], business[1]
        instagram_account_id, instagram_username, access_token = business[5], business[6], business[7]
        try:
            new_token = refresh_long_lived_token(access_token)
            link_instagram_account(business_id, instagram_account_id, instagram_username, new_token)
        except Exception as error:
            # One business's refresh failing (e.g. they revoked access) must not stop
            # the others - worst case its token expires and sending starts failing,
            # which surfaces to that business the next time they try to reply.
            print(f"[{name}] Instagram token refresh failed: {error}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    open(marker, "w").close()
    print("[instagram-token-refresh] Daily refresh pass complete.")


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
            refresh_instagram_tokens_if_needed()
            purge_expired_trash_if_needed()
            send_appointment_reminders_if_needed()
        except Exception as error:
            # Don't let one error (e.g. a temporary network blip) crash the whole loop
            print(f"[{now}] An error occurred: {error}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run_loop()
