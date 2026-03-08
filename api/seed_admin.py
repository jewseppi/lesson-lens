"""Seed an admin user for local development."""
import os
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "lessonlens.db")

EMAIL = "admin@lessonlens.local"
PASSWORD = "adminpassword1"
DISPLAY_NAME = "Admin"

conn = sqlite3.connect(DB_PATH)

existing = conn.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()
if existing:
    print(f"Admin user already exists (id={existing[0]})")
else:
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
        (EMAIL, generate_password_hash(PASSWORD), DISPLAY_NAME),
    )
    conn.commit()
    print(f"Created admin user: {EMAIL} / {PASSWORD}")

conn.close()
