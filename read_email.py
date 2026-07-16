import sys
sys.stdout.reconfigure(encoding="utf-8")

import imaplib
import email
import os
from dotenv import load_dotenv

load_dotenv()

IMAP_SERVER = "imap.gmail.com"


def verify_credentials(email_address, email_app_password):
    """Attempts an IMAP login to Gmail. Returns True if the credentials work, False otherwise.

    Used at registration to immediately confirm the entered email + app password actually
    work (and that the user controls that account), instead of the system silently failing
    later in the background loop. The timeout prevents the request from hanging.
    """
    try:
        connection = imaplib.IMAP4_SSL(IMAP_SERVER, timeout=15)
    except Exception:
        # Network problem - can't confirm either way
        return False

    try:
        connection.login(email_address, (email_app_password or "").replace(" ", ""))
        connection.logout()
        return True
    except Exception:
        # Usually imaplib.IMAP4.error due to wrong credentials
        return False


def get_new_messages(email_address, email_app_password):
    # Connect to Gmail's IMAP server over a secure (SSL) connection
    connection = imaplib.IMAP4_SSL(IMAP_SERVER)
    connection.login(email_address, email_app_password.replace(" ", ""))

    # Select which "folder" we're looking at - INBOX is the main mailbox
    connection.select("INBOX")

    # Only look for UNSEEN (unread) messages
    status, message_ids = connection.search(None, "UNSEEN")
    id_list = message_ids[0].split()

    new_messages = []
    for msg_id in id_list:
        # BODY.PEEK[] (unlike RFC822/BODY[]) does NOT mark the message as read -
        # the owner's real Gmail inbox stays untouched. That means the same message stays
        # UNSEEN forever and reappears on every search - dedup by Message-ID (database.py,
        # processed_messages table) prevents it from being processed more than once.
        status, data = connection.fetch(msg_id, "(BODY.PEEK[])")
        raw_message = data[0][1]
        message = email.message_from_bytes(raw_message)

        new_messages.append({
            "message_id": message.get("Message-ID"),
            "sender": message.get("From"),
            "subject": message.get("Subject"),
            "text": extract_text(message),
        })

    connection.close()
    connection.logout()
    return new_messages


def print_new_messages():
    # Used for manual checking from the terminal (Phase 6) - just prints, returns nothing
    email_address = os.getenv("EMAIL_ADDRESS")
    email_app_password = (os.getenv("EMAIL_APP_PASSWORD") or "").replace(" ", "")
    new_messages = get_new_messages(email_address, email_app_password)

    print(f"Number of unread messages: {len(new_messages)}\n")
    for message in new_messages:
        print(f"From: {message['sender']}")
        print(f"Subject: {message['subject']}")
        print(f"Text: {message['text'][:200]}")
        print("-" * 40)


def extract_text(message):
    # Emails can have multiple parts (text, HTML, attachments) - look for the plain text part
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        return "(no text part)"
    else:
        return message.get_payload(decode=True).decode(errors="ignore")


if __name__ == "__main__":
    print_new_messages()
