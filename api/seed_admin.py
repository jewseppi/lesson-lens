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
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE email = ?",
        (generate_password_hash(PASSWORD, method="scrypt"), EMAIL),
    )
    conn.commit()
    print(f"Admin user already exists (id={existing[0]}), password reset")
else:
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
        (EMAIL, generate_password_hash(PASSWORD, method="scrypt"), DISPLAY_NAME),
    )
    conn.commit()
    print(f"Created admin user: {EMAIL} / {PASSWORD}")

conn.close()
