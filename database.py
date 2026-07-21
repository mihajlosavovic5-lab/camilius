import os
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
    # change). Auto-send defaults OFF - unlike push, this changes what actually happens
    # to a real customer (a reply goes out with no human review), so every existing and
    # new business keeps today's safe approve-then-send flow unless they deliberately
    # turn it on.
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS push_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS auto_send BOOLEAN NOT NULL DEFAULT FALSE")

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


def add_business(name, username, password, contact_email, working_hours, price_list, faq, phone_number=None):
    """Creates the account itself. Instagram isn't connected yet at this point - that
    happens afterward via link_instagram_account(), once the OAuth redirect completes.
    phone_number is optional - purely an extra way to log in later, not required."""
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """INSERT INTO businesses (name, username, password, contact_email, working_hours, price_list, faq, phone_number)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, username, generate_password_hash(password), contact_email, working_hours, price_list, faq, phone_number or None),
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
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, sender, subject, question, suggested_reply, final_reply, status, created_at
        FROM conversations
        WHERE business_id = %s
        ORDER BY created_at DESC
    """, (business_id,))
    result = cursor.fetchall()

    connection.close()
    return result


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
    cursor.execute("DELETE FROM businesses WHERE id = %s", (business_id,))

    connection.commit()
    connection.close()


# --- Subscription (Paddle) ---

# Statuses in which the business RECEIVES the service (worker processes its mail).
# Everything else (none/past_due/paused/canceled) means processing is stopped.
ACTIVE_STATUSES = ("trialing", "active")


def subscription_active(status):
    return status in ACTIVE_STATUSES


def link_subscription(business_id, paddle_customer_id, paddle_subscription_id, status):
    """Called from the webhook the first time Paddle reports a subscription for this business.
    Links the Paddle customer/subscription ID to our business and records the status."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """UPDATE businesses
           SET paddle_customer_id = %s, paddle_subscription_id = %s, subscription_status = %s
           WHERE id = %s""",
        (paddle_customer_id, paddle_subscription_id, status, business_id),
    )
    connection.commit()
    connection.close()


def update_subscription_status(paddle_subscription_id, status):
    """Called from the webhook on later changes (trial->active, past_due, canceled...).
    Finds the business by its already-saved subscription ID and updates just the status."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET subscription_status = %s WHERE paddle_subscription_id = %s",
        (status, paddle_subscription_id),
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
