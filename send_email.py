import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import resend
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

SENDER_ADDRESS = "support@ai-auto-reply-mail.com"


def send_email(recipient, subject, body, business_name, reply_to, html=None):
    message = {
        "from": f"{business_name} <{SENDER_ADDRESS}>",
        "to": [recipient],
        "reply_to": reply_to,
        "subject": subject,
        "text": body,
    }
    if html is not None:
        message["html"] = html
    resend.Emails.send(message)


if __name__ == "__main__":
    send_email(
        recipient=os.getenv("EMAIL_ADDRESS"),
        subject="Test message from the Camilius project",
        body="If you see this, sending email works correctly.",
        business_name="Test business",
        reply_to=os.getenv("EMAIL_ADDRESS"),
    )
    print("Message sent.")
