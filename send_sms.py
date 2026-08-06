import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")


def sms_configured():
    """True once all three Twilio env vars are set (see .env.example) - the single switch
    the phone-verification flow checks before ever trying the SMS path. Empty = the flow
    stays on the emailed-link path it's always used, exactly as if this module didn't
    exist, rather than failing loudly the first time someone tries to verify a phone."""
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def send_sms(to_number, body):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(to=to_number, from_=TWILIO_FROM_NUMBER, body=body)


if __name__ == "__main__":
    send_sms(os.getenv("TWILIO_TEST_NUMBER"), "Test message from the Camilius project.")
    print("Message sent.")
