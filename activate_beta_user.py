"""Manual activation of beta users without Paddle billing.

Beta is free - instead of the user going through checkout, this directly sets
subscription_status (and, optionally, subscription_plan) by username.

Usage: python activate_beta_user.py <username> [status] [plan]
(status defaults to 'active'; can also be 'none'/'canceled' to revoke access.
plan is optional - 'starter' or 'pro'; only matters once status is 'active', since a
'trialing' business already gets full Pro-level access regardless of plan - see
has_pro_access() in database.py. Needed to test Pro-only features like AI appointment
booking on an account whose free trial has already ended.)
"""
import sys
from dotenv import load_dotenv

load_dotenv()

from database import connect, business_by_username


def update_status(username, status, plan=None):
    business = business_by_username(username)
    if business is None:
        print(f"No business with username '{username}'.")
        return

    connection = connect()
    cursor = connection.cursor()
    if plan:
        cursor.execute(
            "UPDATE businesses SET subscription_status = %s, subscription_plan = %s WHERE username = %s",
            (status, plan, username),
        )
    else:
        cursor.execute(
            "UPDATE businesses SET subscription_status = %s WHERE username = %s",
            (status, username),
        )
    connection.commit()
    connection.close()
    print(f"'{business[1]}' ({username}) -> subscription_status = '{status}'" + (f", subscription_plan = '{plan}'" if plan else ""))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3, 4):
        print("Usage: python activate_beta_user.py <username> [status] [plan]")
        sys.exit(1)

    username_arg = sys.argv[1]
    status_arg = sys.argv[2] if len(sys.argv) >= 3 else "active"
    plan_arg = sys.argv[3] if len(sys.argv) == 4 else None
    update_status(username_arg, status_arg, plan_arg)
