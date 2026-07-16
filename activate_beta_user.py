"""Manual activation of beta users without Paddle billing.

Beta is free - instead of the user going through checkout, this directly sets
subscription_status to 'active' by username.

Usage: python activate_beta_user.py <username> [status]
(status defaults to 'active'; can also be 'none'/'canceled' to revoke access)
"""
import sys
from dotenv import load_dotenv

load_dotenv()

from database import connect, business_by_username


def update_status(username, status):
    business = business_by_username(username)
    if business is None:
        print(f"No business with username '{username}'.")
        return

    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE businesses SET subscription_status = %s WHERE username = %s",
        (status, username),
    )
    connection.commit()
    connection.close()
    print(f"'{business[1]}' ({username}) -> subscription_status = '{status}'")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python activate_beta_user.py <username> [status]")
        sys.exit(1)

    username_arg = sys.argv[1]
    status_arg = sys.argv[2] if len(sys.argv) == 3 else "active"
    update_status(username_arg, status_arg)
