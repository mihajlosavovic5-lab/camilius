import os
import ssl
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pg8000.dbapi
from werkzeug.security import generate_password_hash

from encryption import encrypt, decrypt


class UniqueViolation(Exception):
    """Raised when an INSERT/UPDATE would violate a UNIQUE constraint (Postgres SQLSTATE
    23505) - callers (app.py/api.py) catch this the same way they used to catch
    psycopg2.errors.UniqueViolation. pg8000 (unlike psycopg2, which exposes that specific
    exception class directly) only ever raises a generic pg8000.dbapi.DatabaseError whose
    args[0] is the raw error-response field dict keyed by single-letter codes - "C" is
    the SQLSTATE - so _execute_or_raise_unique_violation below is what actually
    translates that into this, right where the violation can occur."""


def _execute_or_raise_unique_violation(cursor, query, params):
    try:
        cursor.execute(query, params)
    except pg8000.dbapi.DatabaseError as error:
        if error.args and isinstance(error.args[0], dict) and error.args[0].get("C") == "23505":
            raise UniqueViolation() from error
        raise


def _pg8000_connect(database_url):
    """pg8000.dbapi.connect() takes individual host/port/user/... keyword arguments
    rather than the single DSN string psycopg2.connect() accepts - this parses
    DATABASE_URL (postgres:// or postgresql://) into those. sslmode is opt-in via the
    URL's own query string: Railway's internal DATABASE_URL (see .railway/railway.ts) is
    a private-network connection between services in the same project and neither sets
    nor needs one, same as CI's local Postgres service container - anything other than
    an explicit "disable"/absent sslmode turns on a default SSL context."""
    parsed = urlparse(database_url)
    query = parse_qs(parsed.query)
    sslmode = (query.get("sslmode") or [None])[0]
    ssl_context = ssl.create_default_context() if sslmode and sslmode != "disable" else None
    return pg8000.dbapi.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        ssl_context=ssl_context,
    )


class _ReusableCursor:
    """Wraps a real pg8000 cursor so fetchone()/fetchall() return the same row shapes
    psycopg2's did - a tuple (or None) from fetchone(), a list of tuples from fetchall().
    pg8000 returns each row as a list, and fetchall() itself as a tuple of those (the
    opposite convention) - every call site in this file that unpacks/slices/concatenates
    a row (e.g. _decrypt_row_token's row[:7] + (token,) + row[8:], or this suite's own
    `== []` assertions against a fetchall() result) assumes the psycopg2 shapes, so this
    is where that gets translated instead of at every one of those call sites."""

    def __init__(self, real_cursor):
        self._real = real_cursor

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def fetchone(self):
        row = self._real.fetchone()
        return tuple(row) if row is not None else None

    def fetchall(self):
        return [tuple(row) for row in self._real.fetchall()]

    @property
    def rowcount(self):
        return self._real.rowcount


class _ReusableConnection:
    """Wraps a real pg8000 connection so every call site's existing connection.close()
    ends the current transaction (commit) instead of tearing the connection down. Only
    .cursor()/.commit()/.closed/.close() are used anywhere in this file, so that's all
    that needs forwarding. pg8000's Connection doesn't expose a public "am I closed"
    flag the way psycopg2's .closed is - closed here reaches into the same private
    _usock the driver itself nulls out in Connection.close(), which is the only signal
    available for connect()'s pre-probe check below."""

    def __init__(self, real_connection):
        self._real = real_connection

    def cursor(self, *args, **kwargs):
        return _ReusableCursor(self._real.cursor(*args, **kwargs))

    def commit(self):
        self._real.commit()

    @property
    def closed(self):
        return self._real._usock is None

    def close(self):
        try:
            self._real.commit()
        except pg8000.dbapi.Error:
            pass  # a broken connection here just means the next connect() call's probe
            # will detect it and open a fresh one instead
        _release_one_db_lock()


_shared_connection = None

# Guards _shared_connection above against two threads touching it at the same time -
# two concurrent queries interleaving on the same wire connection corrupts pg8000's
# protocol state (portal/prepared-statement mismatches, "attempting to use unexecuted
# cursor") instead of just erroring cleanly, and can silently hand one caller another
# caller's row data. Currently a no-op in practice - the Procfile runs gunicorn with its
# default single sync worker/thread, so requests are already fully serialized before
# this ever runs - but that's an assumption living one file away (Procfile) with nothing
# enforcing it here; this is a safety net for if that assumption ever changes (more
# gunicorn workers/threads, a future async caller, a page that fires several concurrent
# sub-requests the way the onboarding wizard's 5 theme-preview iframes do - see
# onboarding_design.html), not a response to an observed single-worker production
# incident.
_db_access_lock = threading.Lock()
# Per-thread nesting depth for _db_access_lock above - a route routinely calls one DB
# helper function from inside another before the outer one's own connection.close() (or
# simply calls several DB functions in a row within one request), so the underlying lock
# is only actually acquired on a thread's first connect() (depth 0->1) and only actually
# released once every matching connection.close() has run (depth back down to 0) -
# _ReusableConnection.close() above calls _release_one_db_lock() for exactly that. This
# releases the lock as soon as each individual DB span finishes, NOT held for a whole
# request's duration - a request that does DB work, then a slow non-DB call (the OpenAI
# request in ai_replies.handle_incoming_message, for instance), then more DB work must
# not hold this lock hostage across that unrelated wait, or concurrent requests would
# serialize on each other's full response time instead of just their actual DB use.
_db_lock_state = threading.local()


def _acquire_db_lock():
    depth = getattr(_db_lock_state, "depth", 0)
    if depth == 0:
        _db_access_lock.acquire()
    _db_lock_state.depth = depth + 1


def _release_one_db_lock():
    depth = getattr(_db_lock_state, "depth", 0)
    if depth <= 0:
        return
    depth -= 1
    _db_lock_state.depth = depth
    if depth == 0:
        _db_access_lock.release()


def release_db_lock():
    """Unconditionally drops this thread's hold on _db_access_lock, regardless of
    remaining nesting depth - called once from app.py's teardown_request so every
    request releases it on the way out no matter how it ended, closing the one gap
    _release_one_db_lock() above can't cover on its own: a route that caught an error
    and skipped its own connection.close() (see connect()'s own SELECT 1 probe/reconnect
    logic for the same "one bad request can't wedge everything" property one layer up).
    Without this, that single skipped close() would leave the lock permanently held for
    every request after it on whichever thread picks them up. A script that never runs
    inside a Flask request (reminders.py's own process, obradi_email.py,
    migrate_to_english.py, ...) never calls this - any such gap there just holds the lock
    for that process's own remaining lifetime, harmless since there's no second thread in
    that process to ever block on it."""
    depth = getattr(_db_lock_state, "depth", 0)
    if depth > 0:
        _db_lock_state.depth = 0
        _db_access_lock.release()


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
    _acquire_db_lock()
    global _shared_connection
    if _shared_connection is not None and _shared_connection.closed == 0:
        try:
            probe = _shared_connection.cursor()
            probe.execute("SELECT 1")
            return _shared_connection
        except pg8000.dbapi.Error:
            # Stale or left in an aborted-transaction state - actually close the real
            # connection before replacing it, so it doesn't linger as an orphaned
            # connection on Postgres's side until garbage collection gets to it.
            try:
                _shared_connection._real.close()
            except pg8000.dbapi.Error:
                pass

    _shared_connection = _ReusableConnection(_pg8000_connect(os.environ["DATABASE_URL"]))
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
    # contact_email should be unique the same way username/phone_number are (see the two
    # unique indexes below) but never actually got one - the app only ever caught
    # duplicates reactively via UniqueViolation (see app.py's register()),
    # which never fired for this column since no DB constraint backed it. That let two
    # businesses register with the identical contact_email, which broke login for both
    # (business_by_login_identifier's OR-across-three-columns query started matching two
    # rows and returned whichever one Postgres felt like - see app.py's register(), which
    # now also pre-checks contact_email_taken()). Guarded rather than unconditional:
    # creating a unique index over data that already has duplicates from before that
    # check existed would fail and crash startup - skip it until any remaining duplicates
    # are cleaned up (find them via /admin/businesses).
    cursor.execute("""
        SELECT 1 FROM businesses WHERE contact_email IS NOT NULL
        GROUP BY contact_email HAVING COUNT(*) > 1 LIMIT 1
    """)
    if cursor.fetchone() is None:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_contact_email_idx ON businesses (contact_email)")
    cursor.execute("ALTER TABLE businesses DROP COLUMN IF EXISTS email_app_password")
    # Instagram/WhatsApp/Messenger DM channels (and their columns below) were retired from
    # the product - the hosted page/widget chat is the only channel now. Columns are left
    # in place rather than dropped (harmless unused data, avoids a destructive migration);
    # nothing reads or writes them anymore.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_account_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_username TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_access_token TEXT")
    # A plain UNIQUE column constraint can only be declared inline at CREATE TABLE time;
    # a unique index gives the same guarantee and works identically whether the table was
    # just created above or already existed from before this migration. Multiple NULLs
    # are allowed under UNIQUE in Postgres.
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_instagram_account_id_idx ON businesses (instagram_account_id)")

    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_display_number TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_access_token TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_whatsapp_phone_number_id_idx ON businesses (whatsapp_phone_number_id)")

    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS messenger_page_id TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS messenger_page_name TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS messenger_page_access_token TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_messenger_page_id_idx ON businesses (messenger_page_id)")

    # Lets the mobile app's Instagram-connect OAuth tab close itself proactively (see
    # mobile/app.js's pollForInstagramConnected and api.py's instagram_connect_state_is_done)
    # instead of depending only on that tab's own window.close(), which isn't reliable on
    # every Custom Tabs provider. Stores the last OAuth "state" token (see
    # make_instagram_connect_state) whose round-trip finished, so the poll can tell "this
    # specific attempt is done" apart from "an Instagram account happens to already be
    # connected" on a RE-connect. Persisted here rather than kept in an in-process dict -
    # this app only runs one gunicorn worker today, but an in-memory flag would silently
    # go missing across a worker restart or a multi-instance deploy, leaving the app
    # polling forever for something the server already forgot happened.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_connect_done_state TEXT")

    # Optional phone number - lets a business log in with username, contact email, OR
    # phone (same "any of these identify you" pattern most consumer apps use), without
    # requiring it at registration. Same unique-but-multiple-NULLs-allowed index pattern.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_number TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_phone_number_idx ON businesses (phone_number)")

    # Push notifications default ON (existing behavior for everyone who already has a
    # device token registered - this is purely an opt-OUT preference, not a safety
    # change).
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS push_enabled BOOLEAN NOT NULL DEFAULT TRUE")

    # Per-type notification toggle under the "Notifications" settings section, alongside
    # push_enabled (the master switch) and reminders_enabled/owner_reminders_enabled (the
    # No-show reminders toggles) - whether the owner gets notified when the AI books a
    # customer in (booking.notify_owner_of_new_booking). Default ON since that's the
    # existing (previously unconditional) behavior for everyone already using AI booking.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS new_booking_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE")

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

    # The business's own IANA timezone, used for every appointment time (see booking.py,
    # which until now had one fixed ZoneInfo("Europe/Belgrade") module-wide because the
    # product was Serbia-only). Defaults to Europe/Belgrade precisely so this migration
    # is a no-op for every existing row - their appointments keep resolving to exactly
    # the same instants they did before. Only businesses that pick something else, and
    # new signups outside Serbia, get different behavior.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Europe/Belgrade'")

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

    # Which channel this conversation came in on - defaults to 'instagram' so every
    # existing row (all of them, before WhatsApp existed) keeps its correct meaning
    # without a backfill. sender_wa_id is WhatsApp's equivalent of sender_igsid above (the
    # id actually needed to send a reply back, via whatsapp_api.send_message) - kept as
    # its own column rather than repurposing sender_igsid for both, so it stays obvious
    # from the schema alone which id a 'whatsapp' row's reply-target actually is.
    # appointments.sender_igsid (see its own comment above) is NOT given a matching
    # sender_wa_id twin - it's reused as-is for a WhatsApp customer's AI-booked
    # appointment too, since nothing there depends on which platform the id came from.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'instagram'")
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sender_wa_id TEXT")
    # Messenger's equivalent of sender_igsid/sender_wa_id above - a PSID (Page-Scoped ID),
    # the id Messenger's Send API needs to reply to a 'messenger' row.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sender_psid TEXT")

    # Sender's Instagram profile picture URL, resolved alongside `sender` at the same time
    # (see instagram_api.get_sender_info) - NULL when the platform has none on file, or the
    # lookup failed, in which case the UI falls back to a letter avatar.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sender_profile_pic TEXT")

    # Archive/Trash (Faza 3) - deliberately separate from `status` (pending/sent), which
    # tracks the reply-approval workflow, not which "folder" a conversation sits in. Both
    # are nullable timestamps rather than booleans so "when" is available for free (e.g.
    # the 30-day trash countdown). A conversation can be archived_at AND later deleted_at
    # (deleting from the Archived tab) - the two aren't mutually exclusive.
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP")
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")

    # expired_at (2026-07-29): set alongside archived_at, but only by
    # expire_stale_pending_conversations() below - never by a user's own Archive action.
    # A conversation still 'pending' 24h after the customer's message can no longer be
    # replied to at all (Instagram's send window has closed - see instagram_api.
    # send_message), so leaving it sitting in the main Pending list forever would just be
    # misleading. Keeping this separate from archived_at lets the UI show a distinct
    # "Expired" badge instead of the normal Archived look, so it reads as "this timed out
    # on its own" rather than "you archived this".
    cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS expired_at TIMESTAMP")

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

    # In-app notifications (bell icon on web, notifications screen on mobile) - currently
    # only owner appointment reminders (see reminders.py), which used to be an email and
    # now land here plus a real push (push_tokens above) instead.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'owner_reminder',
            read_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS notifications_business_created_idx ON notifications (business_id, created_at DESC)")

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

    # Both tables below belonged to the retired Instagram/WhatsApp/Messenger channels
    # (tester-access requests and webhook-delivery diagnostics) - left in place rather
    # than dropped, but nothing reads or writes them anymore.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tester_requests (
            id SERIAL PRIMARY KEY,
            instagram_username TEXT NOT NULL,
            email TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
    # the driver gives for free with an aware column (see booking.py, the only module that
    # writes/reads these). sender_igsid is set only for AI-booked appointments (links back
    # to the source conversation, Instagram or WhatsApp alike - reused as-is for a
    # WhatsApp customer's id too rather than adding a twin sender_wa_id column here, since
    # nothing reading it needs to know which platform it came from); manual bookings made
    # from the Schedule page leave it NULL. status is 'confirmed'/'canceled' -
    # cancellations are soft (kept for history), same spirit as conversations'
    # archived_at/deleted_at.
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

    # Faza 12: reminders_enabled/reminder_24h_sent_at/reminder_2h_sent_at above are kept
    # (unused going forward) rather than dropped, to avoid a destructive migration -
    # reminders_enabled itself is repurposed as "customer reminders enabled" and still
    # actively used. Customer and owner reminders are now independently toggled, each on
    # its own single configurable lead time (in minutes) instead of the old fixed
    # 24h-and-2h pair; reminder_{customer,owner}_sent_at are the new per-channel
    # dedupe markers reminders.py checks instead.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS owner_reminders_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS customer_reminder_minutes INTEGER NOT NULL DEFAULT 1440")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS owner_reminder_minutes INTEGER NOT NULL DEFAULT 60")
    cursor.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_customer_sent_at TIMESTAMP")
    cursor.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_owner_sent_at TIMESTAMP")

    # Email verification at signup. DEFAULT TRUE grandfathers in every account that
    # already existed before this column was added (they never went through a
    # verification step, so there's nothing to gate retroactively) - add_business()
    # below overrides this to FALSE for every new registration going forward, which is
    # the only place a new row is ever inserted with email_verified unset.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email_verify_token TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email_verify_expires TIMESTAMP")
    # Tracked separately from email_verify_expires (rather than derived by subtracting the
    # token's lifetime back off it) so the resend-cooldown check below doesn't have to
    # hardcode that lifetime a second time.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email_verify_sent_at TIMESTAMP")
    # Changing the contact email from an already-verified account (web Settings page)
    # doesn't touch contact_email directly - it stages the new address here and reuses
    # the email_verify_token/expires/sent_at columns above for a confirmation link sent
    # to the NEW address. The old (still-verified) address keeps working until that link
    # is clicked, so a mistyped or unchecked new address can never lock the account out.
    # See save_pending_email_change()/confirm_pending_email_change() below.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS pending_contact_email TEXT")

    # Phone verification (Profile screen, mobile app only - the web Settings page doesn't
    # expose phone_number at all). Unlike email, this never blocks login overall - it only
    # gates whether phone_number itself is usable as a login identifier (see
    # business_by_login_identifier's WHERE clause below); username/email login is
    # unaffected. Same DEFAULT TRUE grandfathering as email_verified above - an existing
    # phone number that was already usable to log in stays usable. add_business() and
    # update_business() below flip this back to FALSE whenever a phone_number is newly set
    # or changed.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_verify_token TEXT")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_verify_expires TIMESTAMP")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone_verify_sent_at TIMESTAMP")

    # Faza 13: mandatory "build your website" wizard right after email verification (see
    # app.py's onboarding_required/onboarding_website_*). NULL means the wizard hasn't
    # been completed yet. Every business that existed before this column was added is
    # grandfathered in (backfilled to its own created_at below, schema_flags-guarded so
    # it only ever runs once) - they already reached the app through the old flow and
    # should never be retroactively forced through the new wizard, same reasoning as
    # email_verified's DEFAULT TRUE grandfathering above.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMP")
    cursor.execute("SELECT 1 FROM schema_flags WHERE key = 'onboarding_backfill_2026_08_04'")
    if not cursor.fetchone():
        cursor.execute("UPDATE businesses SET onboarding_completed_at = created_at WHERE onboarding_completed_at IS NULL")
        cursor.execute("INSERT INTO schema_flags (key) VALUES ('onboarding_backfill_2026_08_04')")

    # "Continue with Google" (web + mobile) - the Google account's own stable `sub` claim,
    # set the first time a business ever logs in/signs up via Google (either linked onto
    # an existing password account that shares the same, Google-verified email, or a
    # brand-new account created on the spot - see app.py's _google_login_or_register).
    # NULL for every account that has never used Google sign-in. Same "unique but multiple
    # NULLs allowed" index pattern as instagram_account_id/phone_number above - appended at
    # the END of the column list (not grouped with `password`/`contact_email` above) for
    # the same positional-tuple reason _BUSINESS_COLUMNS' own comment gives.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS google_id TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS businesses_google_id_idx ON businesses (google_id)")

    # Bridges a Google sign-in completed in the mobile app's system browser tab (Capacitor's
    # Browser plugin - see mobile/app.js's startGoogleLogin) back to the app itself, which
    # has no cookie session in that tab and can't receive a redirect directly. The app opens
    # Google's consent screen with `state` as this row's primary key, then polls
    # /api/google-login/status?state=... (see api.py) until app.py's /auth/google/callback
    # has filled in a token - short-lived and single-use, deleted as soon as either the poll
    # picks up a 'done'/'error' result or it goes stale (see the opportunistic cleanup in
    # api.py). Not used by the web flow at all, which stays on Flask's own session cookie
    # for its (same-browser-tab) redirect round trip, exactly like every other web login.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oauth_login_sessions (
            state TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            token TEXT,
            business_id INTEGER,
            business_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # A Google identity nobody's seen before doesn't get an account created on the spot
    # (see app.py's auth_google_callback/_google_login_existing) - it's handed to the
    # sign-up form as a name/email starting point instead, status='needs_signup' rather
    # than 'done'. google_link_token is google_oauth.make_link_token()'s output, carried
    # through so the mobile app can resubmit it with the sign-up form exactly like the web
    # form's own hidden field does - see complete_oauth_login_session_needs_signup below.
    cursor.execute("ALTER TABLE oauth_login_sessions ADD COLUMN IF NOT EXISTS google_name TEXT")
    cursor.execute("ALTER TABLE oauth_login_sessions ADD COLUMN IF NOT EXISTS google_email TEXT")
    cursor.execute("ALTER TABLE oauth_login_sessions ADD COLUMN IF NOT EXISTS google_link_token TEXT")

    # Account-deletion exit survey (web + mobile "Delete account" wizard) - a snapshot of
    # the business's own identifying fields plus why they left, kept AFTER delete_business()
    # removes the business row itself, so the founder still has something to look at in
    # /admin/account-deletions. No REFERENCES businesses(id) on purpose: that FK would block
    # delete_business()'s own DELETE FROM businesses once this row exists, and the whole
    # point is for this row to outlive the business it describes.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_deletions (
            id SERIAL PRIMARY KEY,
            business_id INTEGER,
            business_name TEXT,
            username TEXT,
            contact_email TEXT,
            instagram_username TEXT,
            phone_number TEXT,
            reason_category TEXT NOT NULL,
            reason_details TEXT,
            source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS account_deletions_created_idx ON account_deletions (created_at DESC)")

    # The business's own hosted public page (camilius.com/p/<slug>) - see pages.py for
    # slug validation/reserved words, which stays out of this module the same way
    # booking.py owns calendar math on top of appointments rather than putting it here.
    # Deliberately does NOT duplicate working_hours/price_list/faq/business_name - the
    # page renders those straight off the businesses row, so there is exactly one
    # knowledge base the AI answers from and the page displays, never two copies that
    # can drift apart. tagline/about are presentation-only extras that have no other home.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_pages (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            slug TEXT NOT NULL,
            published BOOLEAN NOT NULL DEFAULT FALSE,
            tagline TEXT,
            about TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # One page per business for now (the ON CONFLICT target upsert_business_page relies
    # on) - a business wanting more than one page is a later, additive change, not a
    # migration to undo. Slug gets its own unique index since it's looked up directly by
    # /p/<slug> and must be globally unique across every business, not just per-owner.
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS business_pages_business_id_idx ON business_pages (business_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS business_pages_slug_idx ON business_pages (slug)")

    # F5: the one external origin (scheme + host[:port], e.g. "https://example.com", no
    # path) this business has authorized to iframe their /w/<slug> embeddable widget -
    # see pages.py's normalize_widget_domain/app.py's set_security_headers. NULL means
    # "no domain approved yet": /w/<slug> still renders (a business can preview it by
    # navigating there directly), but the CSP frame-ancestors app.py sends for that
    # route falls back to 'none', so no site anywhere can actually embed it in an
    # iframe until this is set. A single origin, not a list, for the same "ship the
    # simple version first" reason business_pages is one page per business - broaden to
    # a list later if a business ever actually asks to embed on more than one site.
    cursor.execute("ALTER TABLE business_pages ADD COLUMN IF NOT EXISTS widget_domain TEXT")

    # Faza 13: which of themes.THEMES' presets this page (and its /w/<slug> widget) is
    # styled with - picked during the onboarding wizard's design step, editable
    # afterward from Settings. Defaults to 'warm' (not the wizard's first-listed option)
    # deliberately - that preset reproduces the exact gold/cream palette
    # public_page.html/widget_frame.html were hardcoded to before this column existed
    # (see themes.py's THEMES['warm']), so every page saved before this migration keeps
    # rendering pixel-identical instead of silently changing color scheme on deploy.
    cursor.execute("ALTER TABLE business_pages ADD COLUMN IF NOT EXISTS theme TEXT NOT NULL DEFAULT 'warm'")

    # Photos/video a business uploads for its own public page (logo in the header, a
    # gallery of shots of the product/space, one hero video) - see storage.py for the
    # actual Cloudinary upload/delete, this table just links the resulting URL back to
    # the business. storage_key is Cloudinary's public_id, kept alongside the URL so a
    # replace/delete can remove the asset from Cloudinary too instead of only forgetting
    # the row and leaving the file (and its storage cost) behind forever. role is
    # 'logo'/'hero_video' (at most one row each - the app enforces that by deleting the
    # existing row before inserting a replacement, same "one row per business" spirit as
    # business_pages above) or 'gallery' (many rows, ordered by position).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_media (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            url TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            media_type TEXT NOT NULL,
            role TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS business_media_business_id_idx ON business_media (business_id)")

    # A business's uploaded source documents (PDF/Word - e.g. a restaurant's menu, a
    # price list, an existing brochure) used as extra factual context for AI page
    # generation - see page_generator.py's generate_page_draft(document_text=...) and
    # business_documents.py/document_extract.py for the upload/extract glue. Text is
    # extracted once at upload time and cached in extracted_text (already capped to
    # document_extract.MAX_EXTRACTED_TEXT_LENGTH) rather than re-parsing the file from
    # Cloudinary on every page-draft generation. storage_key is Cloudinary's public_id
    # (uploaded as resource_type="raw"), same reason as business_media.storage_key above.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_documents (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            url TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            document_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            extracted_text TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS business_documents_business_id_idx ON business_documents (business_id)")

    connection.commit()
    connection.close()


# --- Businesses ---
#
# Canonical row shape returned by business_by_id/business_by_username/
# business_by_instagram_account_id (and, plus subscription_status, all_businesses):
#   0 id, 1 name, 2 username, 3 password (hashed), 4 contact_email, 5 instagram_account_id,
#   6 instagram_username, 7 instagram_access_token (decrypted, None if not connected yet),
#   8 working_hours, 9 price_list, 10 faq, 11 phone_number (None if never set),
#   12 email_verified, 13 phone_verified, 14 pending_contact_email (None unless an email
#   change confirmation is awaiting a click - see save_pending_email_change() below),
#   15 whatsapp_phone_number_id, 16 whatsapp_display_number, 17 whatsapp_access_token
#   (decrypted, None if not connected yet), 18 messenger_page_id, 19 messenger_page_name,
#   20 messenger_page_access_token (decrypted, None if not connected yet), 21 timezone
#   (IANA name, never NULL - see the column's DEFAULT), 22 google_id (None unless this
#   account has ever signed in with Google - see business_by_google_id below). The
#   WhatsApp, Messenger, timezone and google_id columns are appended at the END rather
#   than grouped next to the related ones above, deliberately - this tuple is indexed
#   positionally (business[5], business[7], ...) in well over a hundred places across
#   app.py/api.py/booking.py, so inserting them in the middle would silently shift every
#   existing index instead of just adding new ones. Read index 21 through booking.tz_for
#   (business) rather than directly, so there is exactly one place to fix if the shape
#   ever does change.

_BUSINESS_COLUMNS = """id, name, username, password, contact_email, instagram_account_id,
                       instagram_username, instagram_access_token, working_hours, price_list, faq,
                       phone_number, email_verified, phone_verified, pending_contact_email,
                       whatsapp_phone_number_id, whatsapp_display_number, whatsapp_access_token,
                       messenger_page_id, messenger_page_name, messenger_page_access_token,
                       timezone, google_id"""

# How many columns _BUSINESS_COLUMNS actually selects. Derived rather than written as a
# literal because all_businesses()/all_businesses_unfiltered() below select extra
# non-business columns after it and have to split the row at exactly this boundary -
# a hardcoded 21 there is what silently mis-sliced /admin/businesses the last time this
# constant grew.
_BUSINESS_COLUMN_COUNT = len(_BUSINESS_COLUMNS.split(","))


def _decrypt_row_token(row):
    # instagram_access_token (index 7), whatsapp_access_token (index 17), and
    # messenger_page_access_token (index 20) all need decrypting before use - any of the
    # three can be NULL (that channel not connected yet), not an encrypted empty string.
    # The trailing row[21:] keeps everything appended after the tokens (timezone, and
    # whatever comes next) instead of truncating the tuple at 21 the way an explicit
    # slice would - that truncation is exactly how a newly added column silently goes
    # missing from every business_by_* caller.
    ig_token = decrypt(row[7]) if row[7] else None
    wa_token = decrypt(row[17]) if row[17] else None
    fb_token = decrypt(row[20]) if row[20] else None
    return row[:7] + (ig_token,) + row[8:17] + (wa_token,) + row[18:20] + (fb_token,) + row[21:]


def add_business(name, username, password, contact_email, working_hours, price_list, faq, phone_number=None, initial_status="trialing", push_enabled=True, auto_send=True, new_booking_notifications_enabled=True, reminders_enabled=False, google_id=None, email_verified=False):
    """Creates the account itself. phone_number is optional - purely an extra way to log
    in later, not required.

    subscription_status starts at 'trialing' (not the column's own 'none' default) by
    default - every new account gets our own self-serve 14-day trial immediately, see
    trial_expired()/effective_subscription_active() above. The one exception: once real
    Stripe billing is configured (see app.py's register()), a new account is inserted
    with initial_status='none' instead - access only starts once they've actually put a
    card down via the Stripe Checkout trial (see /subscription's pricing page), which
    checkout.session.completed then flips to 'trialing' for real.

    email_verified is always FALSE here (overriding the column's own DEFAULT TRUE, which
    only exists to grandfather in pre-existing rows) - every new account must confirm its
    contact email via the link save_email_verify_token()/mark_email_verified() below issue
    before app.py's login()/api.py's api_login() will let it in.

    phone_verified is likewise always FALSE - moot when phone_number is None (never usable
    as a login identifier either way, see business_by_login_identifier), but correct if one
    was given: it isn't usable to log in until confirmed via save_phone_verify_token()/
    mark_phone_verified() below, triggered from the Profile screen, not automatically.

    push_enabled/auto_send/new_booking_notifications_enabled/reminders_enabled default to
    whatever each column itself defaults to (True/True/True/False respectively) but the
    mobile app's registration wizard asks up front and passes the answer straight through,
    rather than always signing new accounts up for them and making them opt out (or, for
    reminders_enabled, opt in) afterward in Settings.

    google_id/email_verified are set together by the "Continue with Google" signup path
    (see app.py's _google_login_or_register) for a brand-new account with no existing
    password-based match: google_id links the account for future Google sign-ins, and
    email_verified=True skips the normal confirm-your-email step entirely since Google has
    already proven the address. Every other caller leaves both at their defaults (no
    Google link, unverified - the normal password-signup flow)."""
    connection = connect()
    cursor = connection.cursor()

    _execute_or_raise_unique_violation(
        cursor,
        """INSERT INTO businesses (name, username, password, contact_email, working_hours, price_list, faq, phone_number, subscription_status, email_verified, phone_verified, push_enabled, auto_send, new_booking_notifications_enabled, reminders_enabled, google_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, %s, %s) RETURNING id""",
        (name, username, generate_password_hash(password), contact_email, working_hours, price_list, faq, phone_number or None, initial_status, email_verified, push_enabled, auto_send, new_booking_notifications_enabled, reminders_enabled, google_id),
    )

    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def all_businesses_unfiltered():
    """Every business, including accounts that never finished onboarding. Used by
    /admin/businesses for support/cleanup lookups."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS}, subscription_status, created_at FROM businesses ORDER BY created_at DESC")
    result = cursor.fetchall()
    connection.close()
    return [_decrypt_row_token(row[:_BUSINESS_COLUMN_COUNT]) + row[_BUSINESS_COLUMN_COUNT:] for row in result]


def business_by_id(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE id = %s", (business_id,))
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
    than three separate round-trips.

    The phone_number branch additionally requires phone_verified - an unconfirmed phone
    number can't be used to log in (see phone_verified's own comment in create_database()),
    but this never affects the username/contact_email branches, so an account with an
    unverified (or no) phone number can always still log in the normal way."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE username = %s OR contact_email = %s OR (phone_number = %s AND phone_verified)",
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


def page_chat_session_messages_today(business_id, session_key):
    """How many public-page chat messages this one anonymous visitor session has sent
    today - the per-session leg of public_page_chat()'s (app.py) cost protection,
    alongside the per-IP/per-slug Flask-Limiter throttle and replies_today_count()'s
    existing business-wide daily cap above. Must live in the database, not the in-memory
    limiter: a visitor's session token survives in their browser's localStorage across a
    Railway redeploy, so a limiter reset must not reset this too. Scoped to sender_igsid
    (see ai_replies.py's _SENDER_KEY_KWARG - 'web' reuses the same column Instagram's
    IGSID lives in, there being no reason for a fourth per-channel sender-id column)."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM conversations WHERE business_id = %s AND channel = 'web' "
        "AND sender_igsid = %s AND created_at::date = CURRENT_DATE",
        (business_id, session_key),
    )
    count = cursor.fetchone()[0]

    connection.close()
    return count


def save_draft_reply(business_id, sender, question, suggested_reply, subject=None, sender_igsid=None,
                      profile_pic_url=None, channel="web", sender_wa_id=None, sender_psid=None):
    # subject only ever came from email (there's no such thing on an Instagram DM) - kept
    # as an optional column rather than dropped, since dashboard.html already guards its
    # display with {% if subject %} and removing the column outright isn't worth the churn.
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """INSERT INTO conversations (business_id, sender, subject, question, suggested_reply, sender_igsid, sender_profile_pic, channel, sender_wa_id, sender_psid)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (business_id, sender, subject, question, suggested_reply, sender_igsid, profile_pic_url, channel, sender_wa_id, sender_psid),
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
    # channel/sender_wa_id/sender_psid appended at the end (indices 7, 8, 9) rather than
    # inserted next to sender_igsid - same reasoning as _BUSINESS_COLUMNS above, this
    # tuple is unpacked positionally in app.py/api.py and this keeps every existing index
    # unchanged.
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id, business_id, sender, subject, question, suggested_reply, sender_igsid, channel, sender_wa_id, sender_psid FROM conversations WHERE id = %s",
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
    Trash, which live in their own separate views below. `channel` ('instagram' or
    'whatsapp') is the last column, appended rather than inserted alongside sender_igsid -
    same shared-shape convention as trashed_conversations_for_business's own docstring
    describes, so index 10 stays index 10 everywhere this row is consumed."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at, sender_profile_pic, expired_at, channel
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
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at, sender_profile_pic, expired_at, channel
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
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at, sender_profile_pic, expired_at, channel
        FROM conversations
        WHERE business_id = %s AND deleted_at IS NOT NULL
        ORDER BY deleted_at DESC
    """, (business_id,))
    result = cursor.fetchall()

    connection.close()
    return result


def expire_stale_pending_conversations():
    """Archives every conversation still 'pending' more than 24h after the customer's
    message - Instagram's send API rejects a reply that late no matter what (see
    instagram_api.send_message's own docstring), so there's nothing left to wait for.
    archived_at moves it out of the main Inbox the same way a manual archive would;
    expired_at is set alongside it purely so the UI can tell the two apart and show an
    "Expired" badge instead of treating it like a normal archive. Returns the number of
    conversations expired, for the caller to log.

    Deliberately keyed off expired_at IS NULL rather than archived_at IS NULL - a
    conversation that a business manually archived while still pending, back before this
    column existed, is exactly as unsendable past 24h as one this sweep itself archives,
    but the old WHERE clause's archived_at IS NULL excluded it forever (already archived,
    so never matched, so expired_at stayed NULL and the UI kept showing "Awaiting
    approval" indefinitely). COALESCE(archived_at, NOW()) still moves a genuinely-still-
    pending one out of the main Inbox like before, without stomping the original
    timestamp on one that was already archived manually. deleted_at IS NULL is kept as
    before - a trashed conversation is already out of every active view, so there's
    nothing for expired_at to fix there."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE conversations
        SET archived_at = COALESCE(archived_at, NOW()), expired_at = NOW()
        WHERE status = 'pending' AND expired_at IS NULL AND deleted_at IS NULL
          AND created_at < NOW() - INTERVAL '24 hours'
    """)
    count = cursor.rowcount
    connection.commit()
    connection.close()
    return count


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


def business_by_google_id(google_id):
    """Used by "Continue with Google" - looks up an account that has already signed in
    with this Google account before (see link_google_account below for the first time)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(f"SELECT {_BUSINESS_COLUMNS} FROM businesses WHERE google_id = %s", (google_id,))
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return _decrypt_row_token(row)


def link_google_account(business_id, google_id):
    """First-time "Continue with Google" for an existing, password-based account whose
    contact_email matches the Google account's own (Google-verified) email - see
    app.py's _google_login_or_register. Also marks the email verified: Google has already
    proven the business owns that address, so there's no reason to still gate login behind
    a confirmation link it may never have clicked (see email_verified's own column
    comment)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET google_id = %s, email_verified = TRUE WHERE id = %s",
        (google_id, business_id),
    )
    connection.commit()
    connection.close()


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


# --- "Continue with Google" mobile bridge (oauth_login_sessions) ---
#
# The web flow never touches this table - it stays on Flask's own session cookie for the
# redirect round trip between app.py's /auth/google and /auth/google/callback, same as
# every other web login. Only the mobile app (no cookie session in the system browser tab
# Google's consent screen opens in) needs this: api.py's /api/google-login/start creates
# the pending row, /auth/google/callback fills it in once Google redirects back, and
# /api/google-login/status is what the app polls to pick the result up.

def _cleanup_stale_oauth_login_sessions(cursor):
    """Best-effort GC, called opportunistically from both endpoints below rather than on a
    schedule (this app has no cron worker for anything this size) - a mobile sign-in
    abandoned mid-flow (app killed, consent screen dismissed) would otherwise leave its row
    behind forever. 15 minutes is generous next to how long the whole round trip actually
    takes (well under a minute), so it never races a slow-but-still-in-flight attempt."""
    cursor.execute("DELETE FROM oauth_login_sessions WHERE created_at < NOW() - INTERVAL '15 minutes'")


def create_oauth_login_session(state):
    connection = connect()
    cursor = connection.cursor()
    _cleanup_stale_oauth_login_sessions(cursor)
    cursor.execute("INSERT INTO oauth_login_sessions (state) VALUES (%s)", (state,))
    connection.commit()
    connection.close()


def complete_oauth_login_session(state, token, business_id, business_name):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE oauth_login_sessions SET status = 'done', token = %s, business_id = %s, business_name = %s WHERE state = %s",
        (token, business_id, business_name, state),
    )
    connection.commit()
    connection.close()


def fail_oauth_login_session(state):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE oauth_login_sessions SET status = 'error' WHERE state = %s", (state,))
    connection.commit()
    connection.close()


def complete_oauth_login_session_needs_signup(state, name, email, link_token):
    """A Google identity nobody's seen before (see app.py's _google_login_existing) -
    instead of 'done' with a live token, this hands the mobile app a name/email starting
    point and a google_link_token to resubmit with the sign-up form, exactly like the web
    flow's own redirect to /register?google_name=...&google_link_token=... does."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE oauth_login_sessions SET status = 'needs_signup', google_name = %s, google_email = %s, google_link_token = %s WHERE state = %s",
        (name, email, link_token, state),
    )
    connection.commit()
    connection.close()


def oauth_login_session_exists(state):
    """Lets app.py's /auth/google/callback tell a mobile attempt (a row was created by
    /api/google-login/start) apart from a web one (no row - CSRF-checked via the session
    cookie's own oauth_state instead, see login_google())."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT 1 FROM oauth_login_sessions WHERE state = %s", (state,))
    exists = cursor.fetchone() is not None
    connection.close()
    return exists


def oauth_login_session_status(state):
    """Polled by the mobile app (see api.py's api_google_login_status). A 'done'/'error'/
    'needs_signup' result is single-use - deleted here the moment it's picked up - since
    the token (or google_link_token) inside it is a live credential that should exist in
    exactly one place afterward (the row, or the app that fetched it - never both)."""
    connection = connect()
    cursor = connection.cursor()
    _cleanup_stale_oauth_login_sessions(cursor)
    cursor.execute(
        """SELECT status, token, business_id, business_name, google_name, google_email, google_link_token
           FROM oauth_login_sessions WHERE state = %s""",
        (state,),
    )
    row = cursor.fetchone()
    if row is not None and row[0] in ("done", "error", "needs_signup"):
        cursor.execute("DELETE FROM oauth_login_sessions WHERE state = %s", (state,))
    connection.commit()
    connection.close()

    if row is None:
        return None
    status, token, business_id, business_name, google_name, google_email, google_link_token = row
    return {
        "status": status, "token": token, "business_id": business_id, "business_name": business_name,
        "google_name": google_name, "google_email": google_email, "google_link_token": google_link_token,
    }


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


def save_email_verify_token(business_id, token, expires):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET email_verify_token = %s, email_verify_expires = %s, email_verify_sent_at = NOW() WHERE id = %s",
        (token, expires, business_id),
    )
    connection.commit()
    connection.close()


def business_by_email_verify_token(token):
    # pending_contact_email tells the caller (app.py's verify_email()) which flow this
    # token belongs to: set means an email-change confirmation, NULL means the original
    # signup verification.
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name, username, contact_email, pending_contact_email FROM businesses "
        "WHERE email_verify_token = %s AND email_verify_expires > NOW()",
        (token,),
    )
    row = cursor.fetchone()
    connection.close()
    return row


def mark_email_verified(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET email_verified = TRUE, email_verify_token = NULL, email_verify_expires = NULL WHERE id = %s",
        (business_id,),
    )
    connection.commit()
    connection.close()


def onboarding_completed(business_id):
    """Whether this business has finished the mandatory "build your website" wizard
    (app.py's onboarding_required/onboarding_website_*) - a plain existence check on
    onboarding_completed_at rather than pulling the full business row (business_by_id),
    since this runs on every request to a gated route (see onboarding_required)."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT onboarding_completed_at FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row is not None and row[0] is not None


def mark_onboarding_completed(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET onboarding_completed_at = COALESCE(onboarding_completed_at, NOW()) WHERE id = %s",
        (business_id,),
    )
    connection.commit()
    connection.close()


def save_pending_email_change(business_id, pending_email, token, expires):
    """Stages a new contact email and a fresh confirmation token without touching the
    account's real (still-active) contact_email - see confirm_pending_email_change()."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET pending_contact_email = %s, email_verify_token = %s, "
        "email_verify_expires = %s, email_verify_sent_at = NOW() WHERE id = %s",
        (pending_email, token, expires, business_id),
    )
    connection.commit()
    connection.close()


def confirm_pending_email_change(business_id):
    """Redeems a pending email change: swaps contact_email for the staged address. Raises
    UniqueViolation (same as update_business()) if someone else claimed that address in
    the meantime - the caller is expected to catch it."""
    connection = connect()
    cursor = connection.cursor()
    _execute_or_raise_unique_violation(
        cursor,
        """UPDATE businesses
           SET contact_email = pending_contact_email, pending_contact_email = NULL,
               email_verify_token = NULL, email_verify_expires = NULL
           WHERE id = %s""",
        (business_id,),
    )
    connection.commit()
    connection.close()


def email_verify_resend_wait_seconds(business_id, cooldown_seconds):
    """How many seconds are left before another verification email may be sent (0 once
    the cooldown has elapsed) - lets the resend button's countdown start from the real
    last-sent timestamp instead of only after the user's own click."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM (email_verify_sent_at + (%s * INTERVAL '1 second')) - NOW()))) "
        "FROM businesses WHERE id = %s",
        (cooldown_seconds, business_id),
    )
    row = cursor.fetchone()
    connection.close()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def email_verify_sent_recently(business_id, within_seconds):
    """True if a verification email was issued within the last `within_seconds` - used to
    throttle resends (e.g. hammering the login form with correct credentials but an
    unverified account shouldn't fire a new email on every single attempt)."""
    return email_verify_resend_wait_seconds(business_id, within_seconds) > 0


def save_phone_verify_token(business_id, token, expires):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET phone_verify_token = %s, phone_verify_expires = %s, phone_verify_sent_at = NOW() WHERE id = %s",
        (token, expires, business_id),
    )
    connection.commit()
    connection.close()


def business_by_phone_verify_token(token):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name, phone_number FROM businesses WHERE phone_verify_token = %s AND phone_verify_expires > NOW()",
        (token,),
    )
    row = cursor.fetchone()
    connection.close()
    return row


def mark_phone_verified(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET phone_verified = TRUE, phone_verify_token = NULL, phone_verify_expires = NULL WHERE id = %s",
        (business_id,),
    )
    connection.commit()
    connection.close()


def phone_verify_resend_wait_seconds(business_id, cooldown_seconds):
    """Same idea as email_verify_resend_wait_seconds, for the phone-verification resend
    button on the Profile screen."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM (phone_verify_sent_at + (%s * INTERVAL '1 second')) - NOW()))) "
        "FROM businesses WHERE id = %s",
        (cooldown_seconds, business_id),
    )
    row = cursor.fetchone()
    connection.close()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def phone_verify_sent_recently(business_id, within_seconds):
    """Same throttle as email_verify_sent_recently, for the phone-verification resend
    button on the Profile screen."""
    return phone_verify_resend_wait_seconds(business_id, within_seconds) > 0


def update_business(business_id, name, username, contact_email, phone_number, working_hours, price_list, faq):
    # Reconnecting Instagram is a separate OAuth round-trip (link_instagram_account), not
    # a settings-form field like the old Gmail app password was - nothing to branch on here.
    # username/contact_email/phone_number share the same UNIQUE constraints as at
    # registration - the caller is expected to catch UniqueViolation.
    #
    # The phone_verified/token CASE expressions compare against the row's OLD phone_number
    # (Postgres evaluates every SET expression in an UPDATE against the pre-update row, so
    # this sees the value before the SET phone_number=%s below overwrites it) - any actual
    # change resets verification and invalidates whatever confirmation link was already
    # sent, since it was for a now-superseded number. Leaving it alone when the value is
    # unchanged (the web Settings page always passes the existing phone_number straight
    # through - see app.py's settings()) avoids silently un-verifying a phone number nobody
    # touched. phone_verify_sent_at is reset too, alongside the token - otherwise a resend
    # requested right after changing the number could get silently swallowed by
    # phone_verify_sent_recently's cooldown, which would still be looking at when the OLD
    # number's (now-irrelevant) link was sent.
    connection = connect()
    cursor = connection.cursor()
    _execute_or_raise_unique_violation(
        cursor,
        """UPDATE businesses
           SET name=%s, username=%s, contact_email=%s,
               phone_verified = CASE WHEN phone_number IS DISTINCT FROM %s THEN FALSE ELSE phone_verified END,
               phone_verify_token = CASE WHEN phone_number IS DISTINCT FROM %s THEN NULL ELSE phone_verify_token END,
               phone_verify_expires = CASE WHEN phone_number IS DISTINCT FROM %s THEN NULL ELSE phone_verify_expires END,
               phone_verify_sent_at = CASE WHEN phone_number IS DISTINCT FROM %s THEN NULL ELSE phone_verify_sent_at END,
               phone_number=%s,
               working_hours=%s, price_list=%s, faq=%s
           WHERE id=%s""",
        (
            name, username, contact_email,
            phone_number or None, phone_number or None, phone_number or None, phone_number or None, phone_number or None,
            working_hours, price_list, faq, business_id,
        ),
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
    cursor.execute("DELETE FROM notifications WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM business_media WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM business_documents WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM business_pages WHERE business_id = %s", (business_id,))
    cursor.execute("DELETE FROM businesses WHERE id = %s", (business_id,))

    connection.commit()
    connection.close()


# Slugs offered on the "why are you leaving?" step of the delete-account wizard (web
# templates/delete_account_reason.html and the mobile app's screen-delete-reason) -
# shared here so both app.py and api.py validate against the exact same set instead of
# each hardcoding its own copy that could drift out of sync.
ACCOUNT_DELETION_REASONS = (
    "not_using", "too_expensive", "missing_features", "bugs", "switching", "privacy", "other",
)


def record_account_deletion(business_id, reason_category, reason_details, source):
    """Snapshots the business's own identifying fields plus why they left, called right
    before delete_business() below removes the row for good - this is the only place the
    founder can still see who deleted their account and why (see /admin/account-deletions
    in app.py). reason_category not in ACCOUNT_DELETION_REASONS is the caller's bug, not
    something to silently paper over here - app.py/api.py both validate it first."""
    business = business_by_id(business_id)
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """INSERT INTO account_deletions
               (business_id, business_name, username, contact_email, instagram_username,
                phone_number, reason_category, reason_details, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            business_id,
            business[1] if business else None,
            business[2] if business else None,
            business[4] if business else None,
            business[6] if business else None,
            business[11] if business else None,
            reason_category,
            reason_details or None,
            source,
        ),
    )
    connection.commit()
    connection.close()


def recent_account_deletions(limit=200):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, business_name, username, contact_email, instagram_username,
                  phone_number, reason_category, reason_details, source, created_at
           FROM account_deletions ORDER BY created_at DESC LIMIT %s""",
        (limit,),
    )
    rows = cursor.fetchall()
    connection.close()
    return rows


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


def grant_free_pro_access(business_id):
    """The self-serve "Upgrade to Pro" button shown next to every Pro-locked feature
    (Schedule, no-show reminders, AI upsell) while we're not actually charging anyone
    yet - see BETA_FREE_MODE in app.py. Sets the business straight to an active Pro
    plan, no Paddle/Stripe checkout involved, so has_pro_access() grants access
    immediately regardless of trial state."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET subscription_status = %s, subscription_plan = %s WHERE id = %s",
        ("active", "pro", business_id),
    )
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


# Comma-separated allowlist of contact emails that get full paid access forever without
# ever going through Paddle - the owner's own account, so testing/demoing the site never
# means paying for it. Set/overridden via Railway's COMP_ACCOUNT_EMAILS env var rather
# than only living here, so the list can be changed without a code deploy; the default
# below just means it works out of the box for the one account that needs it today.
def _comp_account_emails():
    raw = os.getenv("COMP_ACCOUNT_EMAILS", "mihajlosavovic5@gmail.com")
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def is_comp_account(business_id):
    business = business_by_id(business_id)
    return bool(business) and business[4].strip().lower() in _comp_account_emails()


def effective_subscription_active(business_id):
    """The single source of truth for "does this business currently have access" -
    subscription_active() (status in trialing/active) plus our own self-serve trial
    expiry on top. Used everywhere access is gated: webhook message processing, the
    dashboard banner, and the subscription page's own active/inactive branch."""
    if is_comp_account(business_id):
        return True
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
    access. Real gating only starts once a specific paid plan exists.

    One query instead of the four separate round trips this used to make (is_comp_account
    -> its own business_by_id, plus get_subscription_status, trial_expired,
    get_subscription_plan) - this is called on nearly every Schedule/API request, so those
    extra round trips were the main source of the Schedule tab's noticeable load delay."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT contact_email, subscription_status, subscription_plan,
                  paddle_subscription_id, stripe_subscription_id, created_at
           FROM businesses WHERE id = %s""",
        (business_id,),
    )
    row = cursor.fetchone()
    connection.close()
    if not row:
        return False
    contact_email, status, plan, paddle_subscription_id, stripe_subscription_id, created_at = row

    if contact_email.strip().lower() in _comp_account_emails():
        return True
    if not subscription_active(status):
        return False
    if status == "trialing" and not paddle_subscription_id and not stripe_subscription_id:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now - created_at > timedelta(days=SELF_SERVE_TRIAL_DAYS):
            return False
    if status == "trialing":
        return True
    return plan == "pro"


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

def is_owner_reminders_enabled(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT owner_reminders_enabled FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return bool(row[0]) if row else False


def set_owner_reminders_enabled(business_id, enabled):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET owner_reminders_enabled = %s WHERE id = %s", (enabled, business_id))
    connection.commit()
    connection.close()


def get_owner_reminder_minutes(business_id):
    """How long before the appointment the owner's reminder notification goes out. Falls
    back to the column's own default (1h) if the business row is somehow missing, same as
    the other reminder getters above."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT owner_reminder_minutes FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else 60


def set_owner_reminder_minutes(business_id, owner_minutes):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET owner_reminder_minutes = %s WHERE id = %s", (owner_minutes, business_id))
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
    """Confirmed appointments starting within the next 8 days (the widest a business can
    configure either lead time to, see app.py's REMINDER_LEAD_MAX_MINUTES) that are still
    missing at least one of their two reminders - across every business. Unlike the old
    24h/2h version, sender_igsid is NOT filtered here: the customer DM reminder still
    needs it (a manually-entered appointment has no DM thread to remind), but the owner
    email reminder doesn't care - reminders.py (the only caller) checks that per-channel."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, business_id, customer_name, sender_igsid, service, start_time,
               reminder_customer_sent_at, reminder_owner_sent_at
        FROM appointments
        WHERE status = 'confirmed'
          AND start_time > NOW() AND start_time <= NOW() + INTERVAL '8 days'
          AND (reminder_customer_sent_at IS NULL OR reminder_owner_sent_at IS NULL)
    """)
    result = cursor.fetchall()
    connection.close()
    return result


def mark_reminder_sent(appointment_id, channel):
    column = "reminder_customer_sent_at" if channel == "customer" else "reminder_owner_sent_at"
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


def is_new_booking_notifications_enabled(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT new_booking_notifications_enabled FROM businesses WHERE id = %s", (business_id,))
    row = cursor.fetchone()
    connection.close()
    return bool(row[0]) if row else False


def set_new_booking_notifications_enabled(business_id, enabled):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("UPDATE businesses SET new_booking_notifications_enabled = %s WHERE id = %s", (enabled, business_id))
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


# --- In-app notifications (bell icon, web + mobile) ---
# Owner-facing appointment reminders (Faza 12, see reminders.py) used to be an email;
# they now land here instead, so they show up in the app itself (web bell icon, mobile
# notifications screen) rather than in an inbox, in addition to a real OS push where
# push_notifications.py has one to send (Android/FCM today). read_at is NULL until the
# owner opens/dismisses it.

def create_notification(business_id, title, body, kind="owner_reminder"):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO notifications (business_id, title, body, kind) VALUES (%s, %s, %s, %s)",
        (business_id, title, body, kind),
    )
    connection.commit()
    connection.close()


def notifications_for_business(business_id, limit=30):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, title, body, kind, created_at, read_at FROM notifications
           WHERE business_id = %s ORDER BY created_at DESC LIMIT %s""",
        (business_id, limit),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def unread_notification_count(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE business_id = %s AND read_at IS NULL",
        (business_id,),
    )
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else 0


def mark_notifications_read(business_id, notification_id=None):
    """Marks a single notification read (notification_id given, scoped to this business
    so one account can't mark another's as read) or, when omitted, every unread
    notification for this business at once (bell dropdown's "mark all read")."""
    connection = connect()
    cursor = connection.cursor()
    if notification_id is None:
        cursor.execute(
            "UPDATE notifications SET read_at = NOW() WHERE business_id = %s AND read_at IS NULL",
            (business_id,),
        )
    else:
        cursor.execute(
            "UPDATE notifications SET read_at = NOW() WHERE business_id = %s AND id = %s AND read_at IS NULL",
            (business_id, notification_id),
        )
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


# --- Business public page ---

def get_business_page(business_id):
    """The owning business's own page row, regardless of published state - used by the
    settings form (an unpublished page must still show its saved slug/tagline/about) and
    by anything that needs to know whether one exists at all. Returns None if the
    business has never saved one."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, business_id, slug, published, tagline, about, created_at, updated_at, widget_domain, theme FROM business_pages WHERE business_id = %s",
        (business_id,),
    )
    result = cursor.fetchone()
    connection.close()
    return result


def page_by_slug(slug):
    """Looked up by the public /p/<slug> and /w/<slug> routes - deliberately returns an
    unpublished page too rather than filtering here, so the route (not this function)
    decides what "not available" means and can 404 identically for "no such slug" and
    "not published yet", never leaking which one it was."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, business_id, slug, published, tagline, about, created_at, updated_at, widget_domain, theme FROM business_pages WHERE slug = %s",
        (slug,),
    )
    result = cursor.fetchone()
    connection.close()
    return result


def upsert_business_page(business_id, slug, published, tagline, about, widget_domain=None, theme=None):
    """Creates the business's page on first save, updates it on every save after - one
    row per business (business_pages_business_id_idx), so this is a plain upsert rather
    than needing a separate create/update pair. Raises UniqueViolation if `slug` is
    already taken by a DIFFERENT business's page (business_pages_slug_idx) - the ON
    CONFLICT target below is business_id alone, so a slug collision can't silently
    resolve as "this business's own page" the way it would if slug were also a conflict
    target here.

    theme=None keeps whatever theme is already saved (falling back to the column's own
    'warm' default only if this business has never saved a page at all) - the
    settings.html profile form saves tagline/about/slug with no theme field at all; only
    the onboarding wizard and settings.html's own theme picker ever pass one explicitly.
    The COALESCE's middle argument is a subquery against this same business_id's
    pre-statement row, evaluated once as part of the INSERT's VALUES list before any
    ON CONFLICT branch runs, so it resolves correctly whether this call ends up doing the
    INSERT or the UPDATE."""
    connection = connect()
    cursor = connection.cursor()
    _execute_or_raise_unique_violation(
        cursor,
        """INSERT INTO business_pages (business_id, slug, published, tagline, about, widget_domain, theme, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s,
                   COALESCE(%s, (SELECT theme FROM business_pages WHERE business_id = %s), 'warm'),
                   CURRENT_TIMESTAMP)
           ON CONFLICT (business_id) DO UPDATE SET
               slug = EXCLUDED.slug, published = EXCLUDED.published,
               tagline = EXCLUDED.tagline, about = EXCLUDED.about, widget_domain = EXCLUDED.widget_domain,
               theme = EXCLUDED.theme,
               updated_at = CURRENT_TIMESTAMP""",
        (business_id, slug, published, tagline, about, widget_domain, theme, business_id),
    )
    connection.commit()
    connection.close()


def published_page_slugs():
    """Every published page's (slug, updated_at) - feeds sitemap.xml's <url> entries."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("SELECT slug, updated_at FROM business_pages WHERE published = TRUE")
    result = cursor.fetchall()
    connection.close()
    return result


# --- Business media (website photos/video - logo, gallery, hero video) ---
# See business_media's own CREATE TABLE comment in create_database() for the shape.
# Row: 0 id, 1 business_id, 2 url, 3 storage_key, 4 media_type, 5 role, 6 position, 7 created_at

def add_business_media(business_id, url, storage_key, media_type, role, position=0):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """INSERT INTO business_media (business_id, url, storage_key, media_type, role, position)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (business_id, url, storage_key, media_type, role, position),
    )
    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def business_media_for_business(business_id):
    """Every media row for this business, gallery images ordered the way they were
    added (position, then insertion order) - used both to render the public page and to
    let the owner see/manage what they've uploaded so far."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, url, storage_key, media_type, role, position, created_at
           FROM business_media WHERE business_id = %s ORDER BY role, position, id""",
        (business_id,),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def business_media_by_role(business_id, role):
    """Existing row(s) for one role (e.g. the current logo) - callers use this before
    replacing a single-slot role (logo/hero_video) to find what needs deleting from
    Cloudinary first, and before adding to the gallery to enforce the count cap."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, url, storage_key, media_type, role, position, created_at
           FROM business_media WHERE business_id = %s AND role = %s ORDER BY position, id""",
        (business_id, role),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def business_media_by_id(media_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, url, storage_key, media_type, role, position, created_at
           FROM business_media WHERE id = %s""",
        (media_id,),
    )
    result = cursor.fetchone()
    connection.close()
    return result


def delete_business_media(media_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM business_media WHERE id = %s", (media_id,))
    connection.commit()
    connection.close()


# --- Business documents (PDF/Word source material for AI page generation) ---
# See business_documents's own CREATE TABLE comment in create_database() for the shape.
# Row: 0 id, 1 business_id, 2 url, 3 storage_key, 4 document_type, 5 filename, 6 extracted_text, 7 created_at

def add_business_document(business_id, url, storage_key, document_type, filename, extracted_text):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """INSERT INTO business_documents (business_id, url, storage_key, document_type, filename, extracted_text)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (business_id, url, storage_key, document_type, filename, extracted_text),
    )
    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def business_documents_for_business(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, url, storage_key, document_type, filename, extracted_text, created_at
           FROM business_documents WHERE business_id = %s ORDER BY id""",
        (business_id,),
    )
    result = cursor.fetchall()
    connection.close()
    return result


def business_document_by_id(document_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, business_id, url, storage_key, document_type, filename, extracted_text, created_at
           FROM business_documents WHERE id = %s""",
        (document_id,),
    )
    result = cursor.fetchone()
    connection.close()
    return result


def delete_business_document(document_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM business_documents WHERE id = %s", (document_id,))
    connection.commit()
    connection.close()


if __name__ == "__main__":
    create_database()
    print("Database is ready.")
