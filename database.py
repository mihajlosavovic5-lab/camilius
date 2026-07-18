import os
import psycopg2
from werkzeug.security import generate_password_hash

from encryption import encrypt, decrypt


def connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def create_database():
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            email_app_password TEXT NOT NULL,
            working_hours TEXT NOT NULL,
            price_list TEXT NOT NULL,
            faq TEXT NOT NULL
        )
    """)

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

    connection.commit()
    connection.close()


# --- Businesses ---

def add_business(name, username, password, email, email_app_password, working_hours, price_list, faq):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """INSERT INTO businesses
           (name, username, password, email, email_app_password, working_hours, price_list, faq)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            name,
            username,
            generate_password_hash(password),
            email,
            encrypt(email_app_password),
            working_hours,
            price_list,
            faq,
        ),
    )

    new_id = cursor.fetchone()[0]
    connection.commit()
    connection.close()
    return new_id


def all_businesses():
    connection = connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, name, email, email_app_password, working_hours, price_list, faq, subscription_status
        FROM businesses
    """)
    result = cursor.fetchall()
    connection.close()

    # email_app_password is encrypted at rest (index 3) - decrypt it before use
    return [row[:3] + (decrypt(row[3]),) + row[4:] for row in result]


def business_by_id(business_id):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name, username, password, email, email_app_password, working_hours, price_list, faq FROM businesses WHERE id = %s",
        (business_id,),
    )
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    # email_app_password is at index 5 - decrypt it before use
    return row[:5] + (decrypt(row[5]),) + row[6:]


def business_by_username(username):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name, username, password, email, email_app_password, working_hours, price_list, faq FROM businesses WHERE username = %s",
        (username,),
    )
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    # NOTE: 'password' (index 3) stays hashed - login checks it via check_password_hash,
    # never decrypted. email_app_password (index 5) is decrypted.
    return row[:5] + (decrypt(row[5]),) + row[6:]


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


def save_draft_reply(business_id, sender, subject, question, suggested_reply):
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        "INSERT INTO conversations (business_id, sender, subject, question, suggested_reply) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (business_id, sender, subject, question, suggested_reply),
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
        "SELECT id, business_id, sender, subject, question, suggested_reply FROM conversations WHERE id = %s",
        (conversation_id,),
    )
    row = cursor.fetchone()

    connection.close()
    return row


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


def business_by_email(email):
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT id, name, username, password, email, email_app_password, working_hours, price_list, faq FROM businesses WHERE email = %s",
        (email,),
    )
    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None
    return row[:5] + (decrypt(row[5]),) + row[6:]


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


def update_business(business_id, name, working_hours, price_list, faq, email_app_password=None):
    connection = connect()
    cursor = connection.cursor()

    # email_app_password is only updated if provided (user changed it); otherwise it stays
    # as-is. When updated, it's re-encrypted.
    if email_app_password is not None:
        cursor.execute(
            """UPDATE businesses
               SET name=%s, working_hours=%s, price_list=%s, faq=%s, email_app_password=%s
               WHERE id=%s""",
            (name, working_hours, price_list, faq, encrypt(email_app_password), business_id),
        )
    else:
        cursor.execute(
            """UPDATE businesses
               SET name=%s, working_hours=%s, price_list=%s, faq=%s
               WHERE id=%s""",
            (name, working_hours, price_list, faq, business_id),
        )

    connection.commit()
    connection.close()


def delete_business(business_id):
    connection = connect()
    cursor = connection.cursor()

    # Conversations first (foreign key to the business), then the business itself
    cursor.execute("DELETE FROM conversations WHERE business_id = %s", (business_id,))
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


if __name__ == "__main__":
    create_database()
    print("Database is ready.")
