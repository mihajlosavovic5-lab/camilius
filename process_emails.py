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
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import time
from datetime import datetime, date

from database import create_database, all_businesses, link_instagram_account
from backup import daily_backup_if_needed
from instagram_api import refresh_long_lived_token

INTERVAL_SECONDS = 3600  # hourly is plenty - both jobs below are self-gated to once/day


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


def run_loop():
    create_database()
    print(f"Started - background worker checking in every {INTERVAL_SECONDS} seconds. (Ctrl+C to stop)")

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            daily_backup_if_needed()
            refresh_instagram_tokens_if_needed()
        except Exception as error:
            # Don't let one error (e.g. a temporary network blip) crash the whole loop
            print(f"[{now}] An error occurred: {error}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run_loop()
