"""SQLite database — users, chat sessions, and messages."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "educare.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't exist."""
    with _get_conn() as conn:
        # Migrate existing databases that predate the claude_session_id column
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN claude_session_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists — nothing to do

        # Migrate existing databases that predate the image_url column
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN image_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists — nothing to do

        # Migrate existing databases that predate the document_url column
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN document_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists — nothing to do

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                email            TEXT UNIQUE NOT NULL,
                username         TEXT UNIQUE NOT NULL,
                hashed_password  TEXT NOT NULL,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title             TEXT NOT NULL DEFAULT 'New Chat',
                claude_session_id TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role         TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content      TEXT NOT NULL,
                image_url    TEXT,
                document_url TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS gmail_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                gmail_email   TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sheets_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS docs_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS drive_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS calendar_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS slides_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@contextmanager
def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _row(conn, query: str, params: tuple = ()) -> dict | None:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def _rows(conn, query: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user(email: str, username: str, hashed_password: str) -> dict | None:
    """Insert a new user. Returns user dict or None if email/username already exists."""
    with _get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)",
                (email, username, hashed_password),
            )
            conn.commit()
            return get_user_by_id(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


def get_user_by_email(email: str) -> dict | None:
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM users WHERE email = ?", (email,))


def get_user_by_username(username: str) -> dict | None:
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM users WHERE username = ?", (username,))


def get_user_by_id(user_id: int) -> dict | None:
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: int, title: str = "New Chat") -> dict:
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (user_id, title) VALUES (?, ?)",
            (user_id, title),
        )
        conn.commit()
        return _row(conn, "SELECT * FROM sessions WHERE id = ?", (cur.lastrowid,))


def get_sessions_for_user(user_id: int) -> list[dict]:
    """Return all sessions for a user, newest first."""
    with _get_conn() as conn:
        return _rows(
            conn,
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )


def get_session(session_id: int, user_id: int) -> dict | None:
    """Fetch a single session, enforcing ownership."""
    with _get_conn() as conn:
        return _row(
            conn,
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )


def rename_session(session_id: int, user_id: int, title: str) -> dict | None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (title, session_id, user_id),
        )
        conn.commit()
        return _row(conn, "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))


def delete_session(session_id: int, user_id: int) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def save_claude_session_id(session_id: int, claude_session_id: str) -> None:
    """Persist the Claude SDK session ID so the next request can resume it."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET claude_session_id = ? WHERE id = ?",
            (claude_session_id, session_id),
        )
        conn.commit()


def _touch_session(conn, session_id: int) -> None:
    conn.execute(
        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,),
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(
    session_id: int,
    role: str,
    content: str,
    image_url: str | None = None,
    document_url: str | None = None,
) -> dict:
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, image_url, document_url) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, image_url, document_url),
        )
        _touch_session(conn, session_id)
        conn.commit()
        return _row(conn, "SELECT * FROM messages WHERE id = ?", (cur.lastrowid,))


def get_messages(session_id: int) -> list[dict]:
    """Return all messages in a session in chronological order."""
    with _get_conn() as conn:
        return _rows(
            conn,
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )


# ---------------------------------------------------------------------------
# Gmail Tokens
# ---------------------------------------------------------------------------

def save_gmail_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    gmail_email: str,
) -> None:
    """Insert or update Gmail OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO gmail_tokens (user_id, access_token, refresh_token, token_expiry, gmail_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                gmail_email   = excluded.gmail_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, gmail_email),
        )
        conn.commit()


def get_gmail_tokens(user_id: int) -> dict | None:
    """Return Gmail token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM gmail_tokens WHERE user_id = ?", (user_id,))


def delete_gmail_tokens(user_id: int) -> bool:
    """Remove Gmail tokens for a user (disconnect Gmail)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM gmail_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sheets Tokens
# ---------------------------------------------------------------------------

def save_sheets_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Sheets OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sheets_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                google_email  = excluded.google_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, google_email),
        )
        conn.commit()


def get_sheets_tokens(user_id: int) -> dict | None:
    """Return Sheets token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM sheets_tokens WHERE user_id = ?", (user_id,))


def delete_sheets_tokens(user_id: int) -> bool:
    """Remove Sheets tokens for a user (disconnect Google Sheets)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM sheets_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Docs Tokens
# ---------------------------------------------------------------------------

def save_docs_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Docs OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO docs_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                google_email  = excluded.google_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, google_email),
        )
        conn.commit()


def get_docs_tokens(user_id: int) -> dict | None:
    """Return Docs token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM docs_tokens WHERE user_id = ?", (user_id,))


def delete_docs_tokens(user_id: int) -> bool:
    """Remove Docs tokens for a user (disconnect Google Docs)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM docs_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Drive Tokens
# ---------------------------------------------------------------------------

def save_drive_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Drive OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO drive_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                google_email  = excluded.google_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, google_email),
        )
        conn.commit()


def get_drive_tokens(user_id: int) -> dict | None:
    """Return Drive token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM drive_tokens WHERE user_id = ?", (user_id,))


def delete_drive_tokens(user_id: int) -> bool:
    """Remove Drive tokens for a user (disconnect Google Drive)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM drive_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Calendar Tokens
# ---------------------------------------------------------------------------

def save_calendar_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Calendar OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO calendar_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                google_email  = excluded.google_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, google_email),
        )
        conn.commit()


def get_calendar_tokens(user_id: int) -> dict | None:
    """Return Calendar token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM calendar_tokens WHERE user_id = ?", (user_id,))


def delete_calendar_tokens(user_id: int) -> bool:
    """Remove Calendar tokens for a user (disconnect Google Calendar)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM calendar_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Slides Tokens
# ---------------------------------------------------------------------------

def save_slides_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Slides OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO slides_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                google_email  = excluded.google_email,
                connected_at  = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, google_email),
        )
        conn.commit()


def get_slides_tokens(user_id: int) -> dict | None:
    """Return Slides token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM slides_tokens WHERE user_id = ?", (user_id,))


def delete_slides_tokens(user_id: int) -> bool:
    """Remove Slides tokens for a user (disconnect Google Slides)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM slides_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0
