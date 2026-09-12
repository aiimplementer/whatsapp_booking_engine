"""
Generates a bcrypt hash for PLATFORM_ADMIN_PASSWORD_HASH in .env.

Usage (from anywhere, with your venv active):
    python app/test/generate_admin_hash.py

It will prompt you for a password (hidden, won't echo to the screen) and
print the hash to paste into .env. Doesn't touch .env itself — copy/paste
the printed line yourself so you don't overwrite anything else in the file.
"""
import getpass
import sys
from pathlib import Path

# Lets `from app.security import ...` resolve when this script is run from
# inside app/test/ instead of the project root — same trick alembic/env.py
# uses. parents[2] from app/test/generate_admin_hash.py is the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.security import hash_password


def main() -> None:
    password = getpass.getpass("Password to hash: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords didn't match — try again.")
        return
    if not password:
        print("Password can't be empty.")
        return

    hashed = hash_password(password)
    print()
    print("Add this line to your .env:")
    print(f"PLATFORM_ADMIN_PASSWORD_HASH={hashed}")


if __name__ == "__main__":
    main()
