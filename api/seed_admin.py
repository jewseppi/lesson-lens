"""Seed an admin user if one does not exist yet.

For local development the default password is "adminpassword1".
In production, set the ADMIN_PASSWORD environment variable to a strong,
unique password.  The script NEVER updates an existing user's password.
"""
import os
import sys
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "lessonlens.db")

EMAIL = "admin@lessonlens.local"
DISPLAY_NAME = "Admin"

# In production ADMIN_PASSWORD must be set as an env var / secret.
# Locally the hardcoded fallback keeps the dev experience frictionless.
PASSWORD = os.environ.get("ADMIN_PASSWORD", "adminpassword1")

if os.environ.get("ADMIN_PASSWORD"):
    print("Using ADMIN_PASSWORD from environment")
else:
    print("No ADMIN_PASSWORD env var — using default dev password")

conn = sqlite3.connect(DB_PATH)

existing = conn.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()
if existing:
    print(f"Admin user already exists (id={existing[0]}), skipping")
else:
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
        (EMAIL, generate_password_hash(PASSWORD, method="scrypt"), DISPLAY_NAME),
    )
    conn.commit()
    print(f"Created admin user: {EMAIL}")

conn.close()
