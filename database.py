import os
from datetime import datetime, timedelta, timezone

import psycopg2
from werkzeug.security import generate_password_hash

from encryption import encrypt, decrypt


class _ReusableConnection:
    """Wraps a real psycopg2 connection so every call site's existing connection.close()
    ends the current transaction (commit) instead of tearing the connection down - a
    plain wrapper because psycopg2's C-implemented connection object doesn't allow
    overriding .close directly (its attributes are read-only). Only .cursor()/.commit()/
    .closed/.close() are used anywhere in this file, so that's all that needs forwarding."""

    def __init__(self, real_connection):
        self._real = real_connection

    def cursor(self, *args, **kwargs):
        return self._real.cursor(*args, **kwargs)

    def commit(self):
        self._real.commit()

    @property
    def closed(self):
        return self._real.closed

    def close(self):
        try:
            self._real.commit()
        except psycopg2.Error:
            pass  # a broken connection here just means the next connect() call's probe
            # will detect it and open a fresh one instead


_shared_connection = None


def connect():
    """Returns a shared, reused Postgres connection instead of opening a brand new one
    (fresh TCP + SSL handshake + auth) on every single call, which is what this used to
    do - every route that touches the DB was paying that full setup cost on every
    request, sometimes more than once per request.

    Every call site still does connection.close() at the end exactly as before - that
    now ends whatever transaction is open (via commit - some call sites, like reads that
    never call .commit() themselves, would otherwise leave a transaction sitting open
    indefinitely now that the connection outlives a single call) instead of tearing the
    connection down, since a single process only ever needs one open connection at a
    time. NOT autocommit: several call sites (e.g. delete_business) run multiple
    statements that must commit together atomically, which autocommit would break by
    committing each statement the instant it runs.

    A cheap SELECT 1 probe catches a connection that's gone stale or was left in an
    aborted-transaction state by some earlier error (e.g. a caught UniqueViolation that
    skipped straight past its own .commit()/.close()) and transparently reconnects, so
    one bad request can't wedge every request after it."""
    global _shared_connection
    if _shared_connection is not None and _shared_connection.closed == 0:
        try:
            with _shared_connection.cursor() as probe:
                probe.execute("SELECT 1")
            return _shared_connection
        except psycopg2.Error:
            # Stale or left in an aborted-transaction state - actually close the real
            # connection before replacing it, so it doesn't linger as an orphaned
            # connection on Postgres's side until garbage collection gets to it.
            try:
                _shared_connection._real.close()
            except psycopg2.Error:
                pass

    _shared_connection = _ReusableConnection(psycopg2.connect(os.environ["DATABASE_URL"]))
    return _shared_connection


def create_database():
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            contact_email TEXT NOT NULL UNIQUE,
            instagram_account_id TEXT,
            instagram_username TEXT,
            instagram_access_token TEXT,
            working_hours TEXT NOT NULL,
            price_list TEXT NOT NULL,
            faq TEXT NOT NULL
        )
    """)

    # Migrating off Gmail/IMAP to Instagram DMs (2026-07-19) - contact_email is a NEW
    # column that replaces the old "email" column's purpose (which used to be both the
    # Gmail inbox we read AND the account-recovery address). Now it's account recovery
    # only; the actual message channel is Instagram, connected separately via OAuth
    # (instagram_account_id/instagram_username/instagram_access_token below). Checking
    # information_schema first (rather than try/except on the UPDATE) avoids leaving the
    # transaction in an aborted state on a fresh database that never had an "email" column.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS contact_email TEXT")
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'businesses' AND column_name = 'email'
    """)
    if cursor.fetchone():
        cursor.execute("UPDATE businesses SET contact_email = email WHERE contact_email IS NULL")
        cursor.execute("ALTER TABLE businesses DROP COLUMN email")
    cursor.execute("ALTER TABLE businesses DROP COLUMN IF EXISTS email_app_password")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_account_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_username TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_access_token TEXT")
    # A plain UNIQUE column constraint can only be declared inline at CREATE TABLE time;
    # a unique index gives the same guarantee and works identically whether the table was
    # just created above or already existed from before this migration. Multiple NULLs
    # (businesses that haven't connected Instagram yet) are allowed under UNIQUE in Postgres.
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_instagram_account_id_idx ON businesses (instagram_account_id)")

    # Optional phone number - lets a business log in with username, contact email, OR
    # phone (same "any of these identify you" pattern most consumer apps use), without
    # requiring it at registration. Same unique-but-multiple-NULLs-allowed index pattern.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_number TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_phone_number_idx ON businesses (phone_number)")

    # Push notifications default ON (existing behavior for everyone who already has a
    # device token registered - this is purely an opt-OUT preference, not a safety
    # change).
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS push_enabled BOOLEAN NOT NULL DEFAULT TRUE")

    # Auto-send originally defaulted OFF (every reply waited for manual approval unless a
    # business deliberately turned this on). Flipped to default ON 2026-07-25 at the
    # product's request, including retroactively for every already-registered business -
    # see the one-time-guarded UPDATE below. That guard matters: unlike the naturally
    # idempotent ADD COLUMN/UPDATE...WHERE-old-value statements elsewhere in this
    # function (safe to rerun forever since their "before" state can never recur), an
    # unconditional "UPDATE businesses SET auto_send = TRUE" run on every deploy would
    # silently re-flip any business that deliberately turns auto-send back off
    # afterward. schema_flags makes sure the retroactive flip fires exactly once, ever.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS auto_send BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE businesses ALTER COLUMN auto_send SET DEFAULT TRUE")
    cursor.execute("CREATE TABLE IF NOT EXISTS schema_flags (key TEXT PRIMARY KEY)")
    cursor.execute("SELECT 1 FROM schema_flags WHERE key = 'auto_send_default_on_2026_07_25'")
    if not cursor.fetchone():
        cursor.execute("UPDATE businesses SET auto_send = TRUE")
        cursor.execute("INSERT INTO schema_flags (key) VALUES ('auto_send_default_on_2026_07_25')")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            sender TEXT NOT NULL,
            subject TEXT,
            question TEXT NOT NULL,
            suggested_reply TEXT NOT NULL,
            final_reply TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ADD COLUMN IF NOT EXISTS - password reset was added after the original schema,
    # this lets the existing production table pick up these columns without a manual migration
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS reset_token TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS reset_expires TIMESTAMP")

    # sender (above) is what's shown in the UI - a resolved Instagram @username where
    # available. sender_igsid is the Instagram-scoped ID actually needed to send a reply
    # back (Instagram's Send API takes that id, not a username) - kept separate since a
    # username isn't necessarily resolvable (or stable) and isn't valid as a send target.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sender_igsid TEXT")

    # Archive/Trash (Faza 3) - deliberately separate from `status` (pending/sent), which
    # tracks the reply-approval workflow, not which "folder" a conversation sits in. Both
    # are nullable timestamps rather than booleans so "when" is available for free (e.g.
    # the 30-day trash countdown). A conversation can be archived_at AND later deleted_at
    # (deleting from the Archived tab) - the two aren't mutually exclusive.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP")
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")

    # Real production bug found 2026-07-20: the 2026-07-16 Serbian->English migration
    # renamed the status VALUES used going forward ('na_cekanju'/'poslato' -> 'pending'/
    # 'sent'), but never touched the conversations.status COLUMN'S DEFAULT constraint
    # itself, which was still the old literal 'na_cekanju' from before the rename -
    # CREATE TABLE IF NOT EXISTS above is a no-op on an already-existing table, so it
    # never applied the "DEFAULT 'pending'" written there. Every conversation created
    # since the migration silently got status='na_cekanju' on insert (save_draft_reply
    # never sets status explicitly, relying on the column default) - a value that matches
    # neither 'pending' nor 'sent' anywhere in app.py/dashboard.html, so those
    # conversations were invisible to the pending-approval UI and instead fell into the
    # template's "else" (sent) branch with an empty final_reply, rendering literally
    # "None" to the business owner. Fixes both the default going forward and every
    # already-stuck row (safe to rerun - both are no-ops once already corrected).
    cursor.execute("ALTER TABLE conversations ALTER COLUMN status SET DEFAULT 'pending'")
    cursor.execute("UPDATE conversations SET status = 'pending' WHERE status = 'na_cekanju'")
    cursor.execute("UPDATE conversations SET status = 'sent' WHERE status = 'poslato'")

    # Paddle subscription - added after the original schema (same reason as reset_token above).
    # subscription_status tracks Paddle's own subscription status: 'none' (never subscribed),
    # 'trialing', 'active', 'past_due', 'paused', 'canceled'. The worker only processes
    # 'trialing'/'active'. Existing businesses default to 'none' and are manually
    # "grandfathered" to 'active' before billing is enabled for them.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS paddle_customer_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS paddle_subscription_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'none'")

    # Faza 5 (2026-07-21): the original schema never had a created_at on businesses at
    # all (only conversations did) - needed now for trial_expired()'s 14-day self-serve
    # trial cutoff below. Existing rows get "now" as their created_at (Postgres fills the
    # DEFAULT in for pre-existing rows on ALTER TABLE), not their real registration date -
    # harmless here, since trial_expired() only ever looks at this for status='trialing'
    # with no paddle_subscription_id yet, which no pre-existing business is in.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Faza 10: Stripe billing, added alongside the existing Paddle columns above rather
    # than reusing them - a stored id needs to be unambiguous about which processor it
    # belongs to. subscription_status/subscription_plan stay processor-agnostic and are
    # shared by both (see link_stripe_subscription/update_stripe_subscription_status).
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT")

    # Card-backed Paddle trial (back on Paddle since Stripe doesn't support a merchant
    # account registered in Serbia - see stripe_billing.py's own note). Paddle Billing has
    # no "trial is about to end" webhook the way Stripe does, so we track the trial's own
    # end date ourselves (from the subscription payload's next_billed_at - see
    # paddle_webhook) and send our own warning email off a plain daily check instead (see
    # reminders.py's send_trial_ending_warnings_if_needed). trial_warning_sent_at dedupes
    # it so the same business never gets the email twice.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS paddle_trial_ends_at TIMESTAMP")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_warning_sent_at TIMESTAMP")

    # Tracks which IMAP messages have already been processed, per business. Needed because
    # the worker reads mail via BODY.PEEK (doesn't mark it read, to leave the owner's inbox
    # untouched) - without this the same unread message would be reprocessed every 60s.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_messages (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            message_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (business_id, message_id)
        )
    """)

    # Lightweight, self-hosted funnel tracking (landing view -> register view -> registered
    # -> first reply sent) - avoids needing a third-party analytics account during beta.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id SERIAL PRIMARY KEY,
            event TEXT NOT NULL,
            business_id INTEGER REFERENCES businesses(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Android app push notification device tokens (Firebase Cloud Messaging).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Self-hosted crash/error reporting from the Android app - avoids needing a
    # third-party crash reporting account during beta.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_errors (
            id SERIAL PRIMARY KEY,
            business_id INTEGER REFERENCES businesses(id),
            message TEXT NOT NULL,
            stack TEXT,
            context TEXT,
            app_version TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Every raw hit on /instagram-webhook is recorded here, purely for diagnosing delivery
    # (does Meta actually call us?) independently of Railway's log viewer, which has been
    # unreliable. Kept small by the trim in log_webhook_hit. Temporary diagnostic aid.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhook_hits (
            id SERIAL PRIMARY KEY,
            note TEXT,
            body TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Faza 8: which Paddle plan (starter/pro) a business is on - separate from
    # subscription_status (which only tracks trialing/active/past_due/etc., not WHICH
    # plan). NULL until a real Paddle checkout completes (see paddle_webhook() in app.py);
    # a business in its own self-serve trial has no plan yet and is treated as full-access
    # by has_pro_access() below regardless. slot_duration_minutes is the appointment
    # length used by the Schedule page/AI booking (booking.py) to lay out bookable slots.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_plan TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS slot_duration_minutes INTEGER NOT NULL DEFAULT 30")

    # Appointments (Faza 8 - Smart Booking/Schedule). start_time/end_time are TIMESTAMPTZ
    # (unlike every naive-UTC TIMESTAMP column elsewhere in this file) deliberately - real
    # wall-clock appointment times need unambiguous, DST-safe timezone handling, which
    # psycopg2 gives for free with an aware column (see booking.py, the only module that
    # writes/reads these). sender_igsid is set only for AI-booked appointments (links back
    # to the Instagram conversation); manual bookings made from the Schedule page leave it
    # NULL. status is 'confirmed'/'canceled' - cancellations are soft (kept for history),
    # same spirit as conversations' archived_at/deleted_at.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            customer_name TEXT NOT NULL,
            sender_igsid TEXT,
            service TEXT,
            start_time TIMESTAMPTZ NOT NULL,
            end_time TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS appointments_business_start_idx ON appointments (business_id, start_time)")

    # Faza 9: No-show reminders (opt-in, off by default - same reasoning as auto_send)
    # and AI upsell ("Service A -> Service B" pairs, free text, same style as
    # working_hours/price_list - see booking.parse_upsell_pairs). reminder_*_sent_at are
    # nullable markers so the reminder loop (reminders.py) never double-sends.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS reminders_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upsell_pairs TEXT")
    cursor.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_24h_sent_at TIMESTAMP")
    cursor.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_2h_sent_at TIMESTAMP")

    connection.commit()
    connection.close()


# --- Businesses ---
#
# Canonical row shape returned by business_by_id/business_by_username/
# business_by_instagram_account_id (and, plus subscription_status, all_businesses):
#   0 id, 1 name, 2 username, 3 password (hashed), 4 contact_email, 5 instagram_account_id,
#   6 instagram_username, 7 instagram_access_token (decrypted, None if not connected yet),
#   8 working_hours, 9 price_list, 10 faq, 11 phone_number (None if never set)

_BUSINESS_COLUMNS = """id, name, username, password, contact_email, instagram_account_id,
                       instagram_username, instagram_access_token, working_hours, price_list, faq,
                       phone_number"""


def _decrypt_row_token(row):
    # instagram_access_token is at index 7 - decrypt it before use. A business that
    # hasn't connected Instagram yet has NULL here, not an encrypted empty string.
    token = decrypt(row[7]) if row[7] else None
    return row[:7] + (token,) + row[8:]


def add_business(name, username, password, contact_email, working_hours, price_list, faq, phone_number=None, initial_status="trialing"):
    """Creates the account itself. Instagram isn't connected yet at this point - that
    happens afterward via link_instagram_account(), once the OAuth redirect completes.
    phone_number is optional - purely an extra way to log in later, not required.

    subscription_status starts at 'trialing' (not the column's own 'none' default) by
    default - every new account gets our own self-serve 14-day trial immediately, see
    trial_expired()/effective_subscription_active() above. The one exception: once real
    Stripe billing is configured (see app.py's register()), a new account is inserted
    with initial_status='none' instead - access only starts once they've actually put a
    card down via the Stripe Checkout trial (see /subscription's pricing page), which
    checkout.session.completed then flips to 'trialing' for real."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """INSERT INTO businesses (name, username, password, contact_email, working_hours, price_list, faq, phone_number, subscription_status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, username, generate_password_hash(password), contact_email, working_hours, price_list, faq, phone_number or None, initial_status),
    )

    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def link_instagram_account(business_id, instagram_account_id, instagram_username, access_token):
    """Saves the Instagram Business account connected via OAuth - the access token is what
    the webhook handler and send-reply code use afterward. Called from the OAuth callback."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """UPDATE businesses
           SET instagram_account_id = %s, instagram_username = %s, instagram_access_token = %s
           WHERE id = %s""",
        (instagram_account_id, instagram_username, encrypt(access_token), business_id),
    )
    connection.commit()
    connection.close()


def all_businesses():
    """Only businesses that have actually connected an Instagram account - used by the
    periodic worker (token-refresh checks, backups), not message ingestion, which is the
    webhook's job now."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"""
        SELECT {_BUSINESS_COLUMNS}, subscription_status
        FROM businesses
        WHERE instagram_access_token IS NOT NULL
    """)
    result = cursor.fetchall()
    connection.close()
    return [_decrypt_row_token(row[:12]) + (row[12],) for row in result]


def all_businesses_unfiltered():
    """Every business regardless of Instagram connection state - unlike all_businesses()
    above, includes accounts that never finished onboarding. Used by /admin/businesses
    for support/cleanup lookups (all_businesses() alone can't find a test/abandoned
    account that never connected Instagram)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS}, subscription_status, created_at FROM businesses ORDER BY created_at DESC")
    result = cursor.fetchall()
    connection.close()
    return [_decrypt_row_token(row[:12]) + (row[12], row[13]) for row in result]


def business_by_id(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


def business_by_instagram_account_id(instagram_account_id):
    """Looks up which business a webhook event belongs to - Instagram identifies the
    message recipient by ITS OWN account id, not any id of ours."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE instagram_account_id = %s", (instagram_account_id,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


def business_by_username(username):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE username = %s",
        (username,),
    )
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    # NOTE: 'password' (index 3) stays hashed - login checks it via check_password_hash,
    # never decrypted.
    return _decrypt_row_token(row)


def business_by_login_identifier(identifier):
    """Login accepts username, contact email, OR phone number - same "any of these
    identifies you" pattern most consumer apps use. One query checking all three rather
    than three separate round-trips."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE username = %s OR contact_email = %s OR phone_number = %s",
        (identifier, identifier, identifier),
    )
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


# --- Conversations ---

def replies_today_count(business_id):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM conversations WHERE business_id = %s AND created_at::date = CURRENT_DATE",
        (business_id,),
    )
    count = cursor.fetchone()[0]

    connection.close()
    return count


def save_draft_reply(business_id, sender, question, suggested_reply, subject=None, sender_igsid=None):
    # subject only ever came from email (there's no such thing on an Instagram DM) - kept
    # as an optional column rather than dropped, since dashboard.html already guards its
    # display with {% if subject %} and removing the column outright isn't worth the churn.
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """INSERT INTO conversations (business_id, sender, subject, question, suggested_reply, sender_igsid)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (business_id, sender, subject, question, suggested_reply, sender_igsid),
    )

    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def already_processed_messages(business_id, message_id_list):
    """Returns the subset of message_id_list that's already been processed for this business."""
    if not message_id_list:
        return set()

    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT message_id FROM processed_messages WHERE business_id = %s AND message_id = ANY(%s)",
        (business_id, list(message_id_list)),
    )
    result = {row[0] for row in cursor.fetchall()}
    connection.close()
    return result


def mark_message_processed(business_id, message_id):
    connection = connect()
    cursor = connection.cursor()
    # ON CONFLICT DO NOTHING - if it's somehow already recorded (e.g. duplicate call), don't error
    cursor.execute(
        "INSERT INTO processed_messages (business_id, message_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (business_id, message_id),
    )
    connection.commit()
    connection.close()


def mark_sent(conversation_id, final_reply):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE conversations SET status = 'sent', final_reply = %s WHERE id = %s",
        (final_reply, conversation_id),
    )

    connection.commit()
    connection.close()


def get_conversation(conversation_id):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id, business_id, sender, subject, question, suggested_reply, sender_igsid FROM conversations WHERE id = %s",
        (conversation_id,),
    )
    row = cursor.fetchone()

    connection.close()
    return row


def raw_conversation_rows(limit=30):
    """Temporary diagnostic - raw status/suggested_reply/final_reply straight from the DB
    for the most recent conversations, used by /admin/conversations-debug to see ground
    truth behind the dashboard's "None" replies. Remove with that endpoint once fixed."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, business_id, status, suggested_reply, final_reply, created_at
        FROM conversations ORDER BY created_at DESC LIMIT %s
    """, (limit,))
    result = cursor.fetchall()
    connection.close()
    return result


def conversations_for_business(business_id):
    """The normal Inbox view (All/Pending/Sent) - excludes anything archived or in the
    Trash, which live in their own separate views below."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at
        FROM conversations
        WHERE business_id = %s AND archived_at IS NULL AND deleted_at IS NULL
        ORDER BY created_at DESC
    """, (business_id,))
    result = cursor.fetchall()

    connection.close()
    return result


def archived_conversations_for_business(business_id):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at
        FROM conversations
        WHERE business_id = %s AND archived_at IS NOT NULL AND deleted_at IS NULL
        ORDER BY archived_at DESC
    """, (business_id,))
    result = cursor.fetchall()

    connection.close()
    return result


def trashed_conversations_for_business(business_id):
    """Same row shape as conversations_for_business/archived_conversations_for_business
    (deliberately - both the web template's tuple-unpacking and the mobile app's
    _serialize_conversation assume one consistent shape everywhere). The UI shows a
    generic "auto-purges after 30 days" note rather than a per-row countdown, so
    deleted_at itself doesn't need to be exposed here - see purge_expired_trash()."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at
        FROM conversations
        WHERE business_id = %s AND deleted_at IS NOT NULL
        ORDER BY deleted_at DESC
    """, (business_id,))
    result = cursor.fetchall()

    connection.close()
    return result


def archive_conversation(conversation_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE conversations SET archived_at = NOW() WHERE id = %s", (conversation_id,))
    connection.commit()
    connection.close()


def unarchive_conversation(conversation_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE conversations SET archived_at = NULL WHERE id = %s", (conversation_id,))
    connection.commit()
    connection.close()


def soft_delete_conversation(conversation_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE conversations SET deleted_at = NOW() WHERE id = %s", (conversation_id,))
    connection.commit()
    connection.close()


def restore_conversation(conversation_id):
    """Pulls a conversation back out of the Trash. Deliberately doesn't touch
    archived_at - restoring something that was archived before it was deleted puts it
    back in the Archived tab, not the main Inbox, which matches how it got there."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE conversations SET deleted_at = NULL WHERE id = %s", (conversation_id,))
    connection.commit()
    connection.close()


def purge_expired_trash():
    """Permanently deletes anything that's been in the Trash for 30+ days. Called once a
    day from process_emails.py's loop, same pattern as the daily backup/token refresh."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM conversations WHERE deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '30 days'")
    purged = cursor.rowcount
    connection.commit()
    connection.close()
    return purged


def business_by_contact_email(contact_email):
    """Used by the forgot-password flow - contact_email is purely for account recovery,
    unrelated to the Instagram account actually being messaged."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE contact_email = %s", (contact_email,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


def business_by_phone_number(phone_number):
    """Used by the "find account by phone number" password-recovery path - looks up the
    account the same way business_by_contact_email does, just by phone_number instead."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE phone_number = %s", (phone_number,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


# --- Live availability checks (Create Account + Profile "already taken" inline errors) ---
#
# Each mirrors one of the UNIQUE constraints on businesses (username, contact_email,
# phone_number) so the frontend can ask "is this taken?" before the user submits the
# form, instead of only finding out from a UniqueViolation after the fact.
# exclude_business_id lets the Profile screen check a field against everyone ELSE's
# value without a business always seeing its own current value as "taken".

def username_taken(username, exclude_business_id=None):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT 1 FROM businesses WHERE username = %s AND id != %s",
        (username, exclude_business_id or 0),
    )
    taken = cursor.fetchone() is not None
    connection.close()
    return taken


def contact_email_taken(contact_email, exclude_business_id=None):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT 1 FROM businesses WHERE contact_email = %s AND id != %s",
        (contact_email, exclude_business_id or 0),
    )
    taken = cursor.fetchone() is not None
    connection.close()
    return taken


def phone_number_taken(phone_number, exclude_business_id=None):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT 1 FROM businesses WHERE phone_number = %s AND id != %s",
        (phone_number, exclude_business_id or 0),
    )
    taken = cursor.fetchone() is not None
    connection.close()
    return taken


def save_reset_token(business_id, token, expires):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET reset_token = %s, reset_expires = %s WHERE id = %s",
        (token, expires, business_id),
    )
    connection.commit()
    connection.close()


def business_by_reset_token(token):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name FROM businesses WHERE reset_token = %s AND reset_expires > NOW()",
        (token,),
    )
    row = cursor.fetchone()
    connection.close()
    return row


def set_new_password(business_id, new_password):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET password = %s, reset_token = NULL, reset_expires = NULL WHERE id = %s",
        (generate_password_hash(new_password), business_id),
    )
    connection.commit()
    connection.close()


def update_business(business_id, name, username, contact_email, phone_number, working_hours, price_list, faq):
    # Reconnecting Instagram is a separate OAuth round-trip (link_instagram_account), not
    # a settings-form field like the old Gmail app password was - nothing to branch on here.
    # username/contact_email/phone_number share the same UNIQUE constraints as at
    # registration - the caller is expected to catch psycopg2.errors.UniqueViolation.
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """UPDATE businesses
           SET name=%s, username=%s, contact_email=%s, phone_number=%s,
               working_hours=%s, price_list=%s, faq=%s
           WHERE id=%s""",
        (name, username, contact_email, phone_number or None, working_hours, price_list, faq, business_id),
    )
    connection.commit()
    connection.close()


def delete_business(business_id):
    connection = connect()
    cursor = connection.cursor()

    # Every table with a business_id foreign key first (order among these doesn't matter,
    # none reference each other - only businesses itself must go last), then the business
    # row. Missing any of these previously made deletion fail with a foreign-key violation
    # for any business that had so much as one analytics event logged (which registration
    # itself always logs) - found while testing the native mobile registration flow.
    cursor.execute("DELETE FROM conversations WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM processed_messages WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM analytics_events WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM push_tokens WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM client_errors WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM appointments WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM businesses WHERE id = %s", (business_id,))

    connection.commit()
    connection.close()


# --- Subscription (Paddle) ---

# Statuses in which the business RECEIVES the service (worker processes its mail).
# Everything else (none/past_due/paused/canceled) means processing is stopped.
ACTIVE_STATUSES = ("trialing", "active")


def subscription_active(status):
    return status in ACTIVE_STATUSES


def link_subscription(business_id, paddle_customer_id, paddle_subscription_id, status, plan=None, trial_ends_at=None):
    """Called from the webhook the first time Paddle reports a subscription for this business.
    Links the Paddle customer/subscription ID to our business and records the status.
    plan ('starter'/'pro') comes from the custom_data set at checkout (see
    templates/subscription.html) - only overwritten when Paddle actually echoes one back,
    so a webhook event that doesn't carry it never wipes out an already-known plan.
    trial_ends_at (from the subscription payload's next_billed_at while trialing) is what
    send_trial_ending_warnings_if_needed (reminders.py) uses to know when to email a
    heads-up before Paddle's own auto-charge - see paddle_trial_ends_at's schema comment."""
    connection = connect()
    cursor = connection.cursor()
    fields = ["paddle_customer_id = %s", "paddle_subscription_id = %s", "subscription_status = %s"]
    params = [paddle_customer_id, paddle_subscription_id, status]
    if plan:
        fields.append("subscription_plan = %s")
        params.append(plan)
    if trial_ends_at:
        fields.append("paddle_trial_ends_at = %s")
        params.append(trial_ends_at)
    params.append(business_id)
    cursor.execute(f"UPDATE businesses SET {', '.join(fields)} WHERE id = %s", params)
    connection.commit()
    connection.close()


def update_subscription_status(paddle_subscription_id, status, plan=None, trial_ends_at=None):
    """Called from the webhook on later changes (trial->active, past_due, canceled...).
    Finds the business by its already-saved subscription ID and updates the status (and
    plan/trial_ends_at, if this event happens to carry one - see link_subscription)."""
    connection = connect()
    cursor = connection.cursor()
    fields = ["subscription_status = %s"]
    params = [status]
    if plan:
        fields.append("subscription_plan = %s")
        params.append(plan)
    if trial_ends_at:
        fields.append("paddle_trial_ends_at = %s")
        params.append(trial_ends_at)
    params.append(paddle_subscription_id)
    cursor.execute(f"UPDATE businesses SET {', '.join(fields)} WHERE paddle_subscription_id = %s", params)
    connection.commit()
    connection.close()


# --- Subscription (Stripe, Faza 10) ---
#
# Parallel to the Paddle functions above rather than a shared/merged implementation -
# each processor's webhook event shapes are different enough (and this app now supports
# either one being linked to a business) that keeping them as separate, independently
# readable functions is safer than forcing them through one abstraction under time
# pressure. Both still write to the same processor-agnostic subscription_status/
# subscription_plan columns that has_pro_access()/effective_subscription_active() read.

def link_stripe_subscription(business_id, stripe_customer_id, stripe_subscription_id, status, plan=None):
    """Called from /stripe-webhook the first time Stripe reports a subscription for this
    business (checkout.session.completed) - see link_subscription (Paddle) above for the
    equivalent Paddle flow this mirrors."""
    connection = connect()
    cursor = connection.cursor()
    if plan:
        cursor.execute(
            """UPDATE businesses
               SET stripe_customer_id = %s, stripe_subscription_id = %s, subscription_status = %s, subscription_plan = %s
               WHERE id = %s""",
            (stripe_customer_id, stripe_subscription_id, status, plan, business_id),
        )
    else:
        cursor.execute(
            """UPDATE businesses
               SET stripe_customer_id = %s, stripe_subscription_id = %s, subscription_status = %s
               WHERE id = %s""",
            (stripe_customer_id, stripe_subscription_id, status, business_id),
        )
    connection.commit()
    connection.close()


def update_stripe_subscription_status(stripe_subscription_id, status, plan=None):
    """Called from /stripe-webhook on later lifecycle events (customer.subscription.
    updated/deleted) - finds the business by its already-saved Stripe subscription id."""
    connection = connect()
    cursor = connection.cursor()
    if plan:
        cursor.execute(
            "UPDATE businesses SET subscription_status = %s, subscription_plan = %s WHERE stripe_subscription_id = %s",
            (status, plan, stripe_subscription_id),
        )
    else:
        cursor.execute(
            "UPDATE businesses SET subscription_status = %s WHERE stripe_subscription_id = %s",
            (status, stripe_subscription_id),
        )
    connection.commit()
    connection.close()


def business_by_stripe_subscription_id(stripe_subscription_id):
    """Looks up a business by its linked Stripe subscription id - used by the
    customer.subscription.trial_will_end webhook handler to find who to email a
    heads-up before their card gets charged (see app.py's stripe_webhook)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE stripe_subscription_id = %s",
        (stripe_subscription_id,),
    )
    row = cursor.fetchone()
    connection.close()
    return _decrypt_row_token(row) if row else None


def get_subscription_plan(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT subscription_plan FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


def get_stripe_customer_id(business_id):
    """Used to open the Stripe Customer Portal (self-service billing) - see
    stripe_billing.create_billing_portal_session. None for a business that has never
    checked out through Stripe (e.g. still trialing, or on the legacy Paddle path)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT stripe_customer_id FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


def get_paddle_customer_id(business_id):
    """Used by the pricing page to show a "you already have a subscription with us"
    style state and to know whether a Manage/cancel note applies. None for a business
    that's never checked out through Paddle yet (still trialing or never started)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT paddle_customer_id FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


def set_subscription_plan(business_id, plan):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET subscription_plan = %s WHERE id = %s", (plan, business_id))
    connection.commit()
    connection.close()


def get_subscription_status(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT subscription_status FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


# New accounts start in our own self-serve 14-day trial (subscription_status='trialing'
# set directly at registration - see add_business() - with no real Paddle subscription
# attached yet). This is a grace period WE invented, distinct from any real trial Paddle
# itself later tracks once a subscription actually exists (paddle_subscription_id set) -
# once that happens, link_subscription()/update_subscription_status() (driven by Paddle's
# own webhooks) own the status from then on and this expiry no longer applies.
SELF_SERVE_TRIAL_DAYS = 14


def trial_expired(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT subscription_status, paddle_subscription_id, stripe_subscription_id, created_at FROM businesses WHERE id = %s",
        (business_id,),
    )
    row = cursor.fetchone()
    connection.close()
    if not row:
        return False
    status, paddle_subscription_id, stripe_subscription_id, created_at = row
    # A business with a real processor subscription attached (Paddle or Stripe) is no
    # longer on our own invented grace period - its card-backed Stripe trial (see
    # stripe_checkout()/checkout.session.completed) ends and converts on Stripe's own
    # schedule, tracked via subscription_status itself, not this 14-day cutoff.
    if status != "trialing" or paddle_subscription_id or stripe_subscription_id:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - created_at > timedelta(days=SELF_SERVE_TRIAL_DAYS)


def effective_subscription_active(business_id):
    """The single source of truth for "does this business currently have access" -
    subscription_active() (status in trialing/active) plus our own self-serve trial
    expiry on top. Used everywhere access is gated: webhook message processing, the
    dashboard banner, and the subscription page's own active/inactive branch."""
    status = get_subscription_status(business_id)
    return subscription_active(status) and not trial_expired(business_id)


# Warn 3 days before Paddle auto-charges a trialing subscription's card - same interval
# Stripe fires its own customer.subscription.trial_will_end at by default (see
# reminders.py's send_trial_ending_warnings_if_needed, which polls on this window since
# Paddle Billing has no equivalent webhook event to react to instead).
TRIAL_WARNING_DAYS_BEFORE = 3


def businesses_needing_trial_warning():
    """Every trialing, Paddle-backed business whose trial ends in ~TRIAL_WARNING_DAYS_
    BEFORE days and hasn't been warned yet. Returns (business_id, name, contact_email,
    paddle_trial_ends_at) rows - reminders.py's caller sends the actual email and then
    calls mark_trial_warning_sent so this never fires twice for the same business."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, name, contact_email, paddle_trial_ends_at FROM businesses
           WHERE subscription_status = 'trialing'
             AND paddle_subscription_id IS NOT NULL
             AND paddle_trial_ends_at IS NOT NULL
             AND trial_warning_sent_at IS NULL
             AND paddle_trial_ends_at <= CURRENT_TIMESTAMP + (%s * INTERVAL '1 day')""",
        (TRIAL_WARNING_DAYS_BEFORE,),
    )
    rows = cursor.fetchall()
    connection.close()
    return rows


def mark_trial_warning_sent(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET trial_warning_sent_at = CURRENT_TIMESTAMP WHERE id = %s", (business_id,))
    connection.commit()
    connection.close()


def has_pro_access(business_id):
    """True when this business's AI should get Pro-only capabilities (currently: the
    Schedule page's smart appointment-booking tool - see booking.py/generate_reply.py).
    A business in its own self-serve trial (no Paddle plan chosen yet) gets full
    Pro-level access for the length of the trial - standard "show the best of the
    product" trial design, same reasoning ACTIVE_STATUSES already applies to basic
    access. Real gating only starts once a specific paid plan exists."""
    status = get_subscription_status(business_id)
    if not subscription_active(status) or trial_expired(business_id):
        return False
    if status == "trialing":
        return True
    return get_subscription_plan(business_id) == "pro"


# --- Appointments / Schedule (Faza 8) ---
#
# Raw CRUD only - slot generation, business-hours parsing, and availability logic live
# in booking.py (which is this module's only caller for these functions), keeping the
# "database.py = SQL only" separation used throughout this file.

def get_slot_duration(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT slot_duration_minutes FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else 30


def set_slot_duration(business_id, minutes):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET slot_duration_minutes = %s WHERE id = %s", (minutes, business_id))
    connection.commit()
    connection.close()


def create_appointment(business_id, customer_name, start_time, end_time, sender_igsid=None, service=None, source="manual", notes=None):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """INSERT INTO appointments (business_id, customer_name, sender_igsid, service, start_time, end_time, source, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (business_id, customer_name, sender_igsid, service, start_time, end_time, source, notes),
    )
    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def get_appointment(appointment_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, business_id, customer_name, start_time, end_time, status, source FROM appointments WHERE id = %s",
        (appointment_id,),
    )
    row = cursor.fetchone()
    connection.close()
    return row


def cancel_appointment(appointment_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE appointments SET status = 'canceled' WHERE id = %s", (appointment_id,))
    connection.commit()
    connection.close()


def appointments_in_range(business_id, start_dt, end_dt):
    """Confirmed appointments overlapping [start_dt, end_dt) - both must be timezone-aware
    datetimes (see booking.py, the only caller). Used both to render the calendar and to
    check availability before a manual or AI booking is confirmed."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, customer_name, sender_igsid, service, start_time, end_time, status, source, notes
           FROM appointments
           WHERE business_id = %s AND status = 'confirmed' AND start_time < %s AND end_time > %s
           ORDER BY start_time""",
        (business_id, end_dt, start_dt),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def all_appointments_for_business(business_id):
    """Every appointment (any status, any date - unlike appointments_in_range, which is
    scoped to a window) for a business's own data export - see app.py's
    business_export_data()."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT customer_name, service, start_time, end_time, status, source, notes, created_at
           FROM appointments WHERE business_id = %s ORDER BY start_time""",
        (business_id,),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def next_upcoming_appointment_for_sender(business_id, sender_igsid):
    """The customer's next confirmed appointment with this business, if any - used by the
    AI's cancel/reschedule tool (see booking.attempt_ai_cancel_or_reschedule) to find
    which appointment a DM conversation is actually about, since the model never has an
    appointment ID to work with, only the Instagram thread it's already in."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, customer_name, service, start_time, end_time
           FROM appointments
           WHERE business_id = %s AND sender_igsid = %s AND status = 'confirmed' AND start_time > NOW()
           ORDER BY start_time LIMIT 1""",
        (business_id, sender_igsid),
    )
    row = cursor.fetchone()
    connection.close()
    return row


# --- No-show reminders / AI upsell (Faza 9) ---

def is_reminders_enabled(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT reminders_enabled FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return bool(row[0]) if row else False


def set_reminders_enabled(business_id, enabled):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET reminders_enabled = %s WHERE id = %s", (enabled, business_id))
    connection.commit()
    connection.close()


def get_upsell_pairs(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT upsell_pairs FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else None


def set_upsell_pairs(business_id, text):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET upsell_pairs = %s WHERE id = %s", (text, business_id))
    connection.commit()
    connection.close()


def appointments_needing_reminders():
    """Confirmed, Instagram-DM-originated appointments (sender_igsid set - a manually
    entered appointment has no DM thread to remind) starting within the next 25 hours
    that are still missing at least one of their two reminders - across every business.
    reminders.py (the only caller) decides, per row, exactly which window (24h/2h, if
    either) currently applies and whether that business even has reminders turned on."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, business_id, customer_name, sender_igsid, service, start_time,
               reminder_24h_sent_at, reminder_2h_sent_at
        FROM appointments
        WHERE status = 'confirmed' AND sender_igsid IS NOT NULL
          AND start_time > NOW() AND start_time <= NOW() + INTERVAL '25 hours'
          AND (reminder_24h_sent_at IS NULL OR reminder_2h_sent_at IS NULL)
    """)
    result = cursor.fetchall()
    connection.close()
    return result


def mark_reminder_sent(appointment_id, which):
    column = "reminder_24h_sent_at" if which == "24h" else "reminder_2h_sent_at"
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"UPDATE appointments SET {column} = NOW() WHERE id = %s", (appointment_id,))
    connection.commit()
    connection.close()


def is_push_enabled(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT push_enabled FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return bool(row[0]) if row else False


def set_push_enabled(business_id, enabled):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET push_enabled = %s WHERE id = %s", (enabled, business_id))
    connection.commit()
    connection.close()


def is_auto_send_enabled(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT auto_send FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return bool(row[0]) if row else False


def set_auto_send(business_id, enabled):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET auto_send = %s WHERE id = %s", (enabled, business_id))
    connection.commit()
    connection.close()


def business_stats(business_id):
    """Sent-reply counts used for the dashboard stats bar - total and last 7 days."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'sent') AS total_sent,
            COUNT(*) FILTER (WHERE status = 'sent' AND created_at >= NOW() - INTERVAL '7 days') AS sent_this_week
        FROM conversations
        WHERE business_id = %s
    """, (business_id,))
    total_sent, sent_this_week = cursor.fetchone()
    connection.close()
    return {"total_sent": total_sent, "sent_this_week": sent_this_week}


def previous_conversations_with(business_id, sender, limit=5):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT question, final_reply
        FROM conversations
        WHERE business_id = %s AND sender = %s AND status = 'sent'
        ORDER BY created_at DESC
        LIMIT %s
    """, (business_id, sender, limit))
    result = cursor.fetchall()

    connection.close()
    return list(reversed(result))


# --- Analytics (self-hosted, minimal) ---

def log_event(event, business_id=None):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO analytics_events (event, business_id) VALUES (%s, %s)",
        (event, business_id),
    )
    connection.commit()
    connection.close()


def event_funnel(days=30):
    """Counts per event type in the last `days` days - the basic signup funnel."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT event, COUNT(*)
        FROM analytics_events
        WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
        GROUP BY event
    """, (days,))
    result = dict(cursor.fetchall())
    connection.close()
    return result


# --- Push notification tokens ---

def save_push_token(business_id, token):
    connection = connect()
    cursor = connection.cursor()
    # A token belongs to one device; if that device previously registered under a
    # different business (e.g. logged out and a different business logged in on the
    # same phone), move it rather than erroring on the UNIQUE constraint.
    cursor.execute(
        """INSERT INTO push_tokens (business_id, token) VALUES (%s, %s)
           ON CONFLICT (token) DO UPDATE SET business_id = EXCLUDED.business_id""",
        (business_id, token),
    )
    connection.commit()
    connection.close()


def push_tokens_for_business(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT token FROM push_tokens WHERE business_id = %s", (business_id,))
    result = [row[0] for row in cursor.fetchall()]
    connection.close()
    return result


def remove_push_token(token):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM push_tokens WHERE token = %s", (token,))
    connection.commit()
    connection.close()


# --- Client-side crash/error reporting ---

def log_client_error(business_id, message, stack, context, app_version):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """INSERT INTO client_errors (business_id, message, stack, context, app_version)
           VALUES (%s, %s, %s, %s, %s)""",
        (business_id, message, stack, context, app_version),
    )
    connection.commit()
    connection.close()


def recent_client_errors(limit=100):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, business_id, message, stack, context, app_version, created_at
        FROM client_errors ORDER BY created_at DESC LIMIT %s
    """, (limit,))
    result = cursor.fetchall()
    connection.close()
    return result


# --- Webhook hit logging (temporary diagnostic) ---

def log_webhook_hit(note, body):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("INSERT INTO webhook_hits (note, body) VALUES (%s, %s)", (note, body[:5000]))
    # Keep only the most recent 50 so this diagnostic table can't grow unbounded
    cursor.execute("""
        DELETE FROM webhook_hits WHERE id NOT IN (
            SELECT id FROM webhook_hits ORDER BY created_at DESC LIMIT 50
        )
    """)
    connection.commit()
    connection.close()


def recent_webhook_hits(limit=20):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, note, body, created_at FROM webhook_hits ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    result = cursor.fetchall()
    connection.close()
    return result


if __name__ == "__main__":
    create_database()
    print("Database is ready.")
