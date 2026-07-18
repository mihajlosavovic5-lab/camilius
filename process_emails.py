import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import time
from datetime import datetime
from email.utils import parseaddr

from read_email import get_new_messages
from generate_reply import generate_reply
from send_email import send_email
from database import (
    create_database, save_draft_reply, all_businesses, replies_today_count, previous_conversations_with,
    subscription_active, already_processed_messages, mark_message_processed,
)
from backup import daily_backup_if_needed
from push_notifications import send_push_to_business

DAILY_REPLY_LIMIT = 50

# Email address that receives alerts when processing a business fails (e.g. yours)
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

# So we don't spam alerts every 60s for the same problem: at most one alert
# per business every ALERT_COOLDOWN_SECONDS. Kept in the process's memory.
ALERT_COOLDOWN_SECONDS = 6 * 60 * 60
_last_alert = {}


def send_alert(business_name, error):
    if not ALERT_EMAIL:
        return

    now = time.time()
    last = _last_alert.get(business_name, 0)
    if now - last < ALERT_COOLDOWN_SECONDS:
        return

    try:
        send_email(
            recipient=ALERT_EMAIL,
            subject=f"[Camilius AI ALERT] Error for business: {business_name}",
            body=(
                f"Processing messages for business '{business_name}' failed.\n\n"
                f"Error: {error}\n\n"
                f"Other businesses are processing normally. If the error is an IMAP login\n"
                f"failure, the business probably changed their Gmail password or revoked the app password."
            ),
            business_name="Camilius AI system",
            reply_to=ALERT_EMAIL,
        )
        _last_alert[business_name] = now
    except Exception as e:
        # An alert failure must never crash the loop - just log it
        print(f"[ALERT] Failed to send alert: {e}")


def process_one_business(business_row):
    business_id, name, email, email_app_password, working_hours, price_list, faq, subscription_status = business_row

    if not subscription_active(subscription_status):
        print(f"[{name}] Subscription not active ({subscription_status}) - skipping.")
        return

    remaining_today = DAILY_REPLY_LIMIT - replies_today_count(business_id)
    if remaining_today <= 0:
        print(f"[{name}] Daily limit reached ({DAILY_REPLY_LIMIT}) - skipping until tomorrow.")
        return

    business = {
        "business_name": name,
        "working_hours": working_hours,
        "price_list": price_list,
        "faq": faq,
    }

    all_messages = get_new_messages(email, email_app_password)

    # Messages stay UNREAD in Gmail (see read_email.py) so they come back every cycle -
    # filter out the ones we already processed last cycle, then apply the daily limit
    # to the actually-new ones.
    processed = already_processed_messages(business_id, [m["message_id"] for m in all_messages if m["message_id"]])
    new_messages = [m for m in all_messages if m["message_id"] not in processed][:remaining_today]
    print(f"[{name}] New message count: {len(new_messages)}")

    for message in new_messages:
        sender_name, sender_address = parseaddr(message["sender"])

        print(f"[{name}] Processing message from: {sender_address}")

        history = previous_conversations_with(business_id, sender_address)
        suggested_reply = generate_reply(message["text"], business, history=history)

        new_conversation_id = save_draft_reply(business_id, sender_address, message["subject"], message["text"], suggested_reply)
        if message["message_id"]:
            mark_message_processed(business_id, message["message_id"])
        print(f"[{name}] Draft saved, waiting for approval in the dashboard.")

        try:
            send_push_to_business(
                business_id,
                title="New customer question",
                body=f"{sender_name or sender_address}: {message['text'][:80]}",
                data={"conversation_id": str(new_conversation_id)},
            )
        except Exception as e:
            # A push failure must never block draft processing
            print(f"[{name}] Push notification failed: {e}")


def process_new_messages():
    for business_row in all_businesses():
        # A failure for one business (e.g. a changed Gmail password) must not
        # stop processing of the other businesses in the same cycle
        try:
            process_one_business(business_row)
        except Exception as error:
            name = business_row[1]
            print(f"[{name}] Error while processing - skipping this business: {error}")
            send_alert(name, error)


INTERVAL_SECONDS = 60


def run_loop():
    create_database()
    print(f"Started - checking for new messages every {INTERVAL_SECONDS} seconds. (Ctrl+C to stop)")

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"\n[{now}] Checking for new messages...")
            process_new_messages()
            daily_backup_if_needed()
        except Exception as error:
            # Don't let one error (e.g. a temporary internet outage) crash the whole program
            print(f"[{now}] An error occurred: {error}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run_loop()
