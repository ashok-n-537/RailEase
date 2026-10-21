
import sqlite3
from getpass import getpass
from app import DATABASE

email = input("Enter your registered email: ").strip()

confirmation = input(
    "Type MAKE-ADMIN in capital letters to confirm: "
).strip()

if confirmation != "MAKE-ADMIN":
    print("Confirmation did not match. Please run the script again.")
    raise SystemExit(1)

connection = sqlite3.connect(DATABASE)

try:
    user = connection.execute(
        "SELECT id, email FROM users WHERE email = ? COLLATE NOCASE",
        (email,)
    ).fetchone()

    if user is None:
        print("No account found for that email.")
        print("Register the account in RailEase first.")
    else:
        connection.execute(
            "UPDATE users SET role = 'admin' WHERE id = ?",
            (user[0],)
        )
        connection.commit()
        print(f"Success! Admin role granted to {user[1]}.")

finally:
    connection.close()
