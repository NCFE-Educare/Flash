"""SQLite database — users, chat sessions, and messages."""

import os
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

# Use DB_PATH env var for deployment (e.g. /opt/educare/data/educare.db); else project root
_db_path = os.environ.get("DB_PATH")
DB_PATH = Path(_db_path) if _db_path else Path(__file__).parent / "educare.db"
if _db_path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


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

        # Migrate sessions to allow NULL user_id (Google Chat groups)
        try:
            # Check if sessions table exists first
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchall()
            if tables:
                # Check if user_id is already nullable
                info = conn.execute("PRAGMA table_info(sessions)").fetchall()
                user_id_col = next((c for c in info if c[1] == "user_id"), None)
                if user_id_col and user_id_col[3] == 1:  # 1 means NOT NULL
                    print("[DB] sessions.user_id is NOT NULL, migrating...")
                    # 1. Rename old table
                    conn.execute("ALTER TABLE sessions RENAME TO sessions_old")
                    # 2. Create new table
                    conn.execute("""
                        CREATE TABLE sessions (
                            id                INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id           INTEGER REFERENCES users(id) ON DELETE CASCADE,
                            title             TEXT NOT NULL DEFAULT 'New Chat',
                            claude_session_id TEXT,
                            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    # 3. Copy data
                    conn.execute("""
                        INSERT INTO sessions (id, user_id, title, claude_session_id, created_at, updated_at)
                        SELECT id, user_id, title, claude_session_id, created_at, updated_at FROM sessions_old
                    """)
                    # 4. Drop old table
                    conn.execute("DROP TABLE sessions_old")
                    conn.commit()
                    print("[DB] Migrated sessions table to allow NULL user_id successfully")
        except Exception as e:
            print(f"[DB] Migration error (sessions nullability): {e}")
            conn.rollback() # Rollback on error

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
                user_id           INTEGER REFERENCES users(id) ON DELETE CASCADE,
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

            CREATE TABLE IF NOT EXISTS forms_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS meet_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS classroom_tokens (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT NOT NULL,
                google_email  TEXT NOT NULL,
                connected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                remind_at    TEXT NOT NULL,
                message      TEXT NOT NULL,
                delivered    INTEGER NOT NULL DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                identifier  TEXT NOT NULL,
                title       TEXT NOT NULL,
                type        TEXT NOT NULL,
                language    TEXT,
                content     TEXT NOT NULL,
                version     INTEGER NOT NULL DEFAULT 1,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, identifier)
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

def create_session(user_id: int | None, title: str = "New Chat") -> dict:
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


def get_session(session_id: int, user_id: int | None) -> dict | None:
    """Fetch a single session, enforcing ownership if user_id is provided."""
    with _get_conn() as conn:
        if user_id is not None:
            return _row(
                conn,
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        return _row(conn, "SELECT * FROM sessions WHERE id = ? AND user_id IS NULL", (session_id,))


def rename_session(session_id: int, user_id: int | None, title: str) -> dict | None:
    with _get_conn() as conn:
        if user_id is not None:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (title, session_id, user_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id IS NULL",
                (title, session_id),
            )
        conn.commit()
        return get_session(session_id, user_id)


def delete_session(session_id: int, user_id: int | None) -> bool:
    with _get_conn() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        else:
            cur = conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id IS NULL",
                (session_id,),
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


def get_session_by_title(title: str, user_id: int | None = None) -> dict | None:
    """Fetch an existing session by title (useful for space-based session reuse)."""
    with _get_conn() as conn:
        if user_id is not None:
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title = ? AND user_id = ? LIMIT 1",
                (title, user_id)
            )
        else:
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title = ? AND user_id IS NULL LIMIT 1",
                (title,)
            )


def _touch_session(conn, session_id: int) -> None:
    conn.execute(
        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,),
    )


def get_or_create_gchat_session(space_name: str, user_email: str, is_group: bool) -> dict:
    """
    Fetch or create a session specifically for a Google Chat space.
    If is_group=True, we use user_id=None to keep the space history shared/shared.
    If is_group=False, we still use the space_name (which is a DM ID) as the title.
    """
    # Try to find existing session by space name (title)
    session = get_session_by_title(space_name, user_id=None)
    if session:
        return session
    
    # Create new one
    return create_session(user_id=None, title=space_name)


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


# ---------------------------------------------------------------------------
# Forms Tokens
# ---------------------------------------------------------------------------

def save_forms_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Forms OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO forms_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
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


def get_forms_tokens(user_id: int) -> dict | None:
    """Return Forms token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM forms_tokens WHERE user_id = ?", (user_id,))


def delete_forms_tokens(user_id: int) -> bool:
    """Remove Forms tokens for a user (disconnect Google Forms)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM forms_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Meet Tokens
# ---------------------------------------------------------------------------

def save_meet_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Meet OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO meet_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
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


def get_meet_tokens(user_id: int) -> dict | None:
    """Return Meet token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM meet_tokens WHERE user_id = ?", (user_id,))


def delete_meet_tokens(user_id: int) -> bool:
    """Remove Meet tokens for a user (disconnect Google Meet)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM meet_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Classroom Tokens
# ---------------------------------------------------------------------------

def save_classroom_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    google_email: str,
) -> None:
    """Insert or update Google Classroom OAuth tokens for a user (upsert)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO classroom_tokens (user_id, access_token, refresh_token, token_expiry, google_email)
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


def get_classroom_tokens(user_id: int) -> dict | None:
    """Return Classroom token dict for a user, or None if not connected."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM classroom_tokens WHERE user_id = ?", (user_id,))


def delete_classroom_tokens(user_id: int) -> bool:
    """Remove Classroom tokens for a user (disconnect Google Classroom)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM classroom_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def create_reminder(user_id: int, remind_at: str, message: str) -> dict:
    """Insert a reminder. remind_at: ISO datetime string. Returns the created row."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, remind_at, message) VALUES (?, ?, ?)",
            (user_id, remind_at, message),
        )
        conn.commit()
        return _row(conn, "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,))


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def create_artifact(
    session_id: int,
    user_id: int,
    identifier: str,
    title: str,
    type: str,
    content: str,
    language: str | None = None,
) -> dict:
    """Insert a new artifact or fail if (session_id, identifier) exists."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO artifacts (session_id, user_id, identifier, title, type, language, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, identifier, title, type, language, content),
        )
        _touch_session(conn, session_id)
        conn.commit()
        return _row(conn, "SELECT * FROM artifacts WHERE id = ?", (cur.lastrowid,))


def update_artifact(
    session_id: int,
    identifier: str,
    content: str,
    title: str | None = None,
) -> dict | None:
    """Update an artifact's content and increment version."""
    with _get_conn() as conn:
        if title:
            conn.execute(
                """
                UPDATE artifacts
                SET content = ?, title = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND identifier = ?
                """,
                (content, title, session_id, identifier),
            )
        else:
            conn.execute(
                """
                UPDATE artifacts
                SET content = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND identifier = ?
                """,
                (content, session_id, identifier),
            )
        _touch_session(conn, session_id)
        conn.commit()
        return get_artifact_by_identifier(session_id, identifier)


def get_artifact_by_identifier(session_id: int, identifier: str) -> dict | None:
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM artifacts WHERE session_id = ? AND identifier = ?", (session_id, identifier))


def get_artifacts_for_session(session_id: int) -> list[dict]:
    with _get_conn() as conn:
        return _rows(conn, "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at ASC", (session_id,))


def get_reminders_for_user(user_id: int, include_delivered: bool = True) -> list[dict]:
    """Return reminders for a user, ordered by remind_at ascending."""
    with _get_conn() as conn:
        if include_delivered:
            return _rows(
                conn,
                "SELECT * FROM reminders WHERE user_id = ? ORDER BY remind_at ASC",
                (user_id,),
            )
        return _rows(
            conn,
            "SELECT * FROM reminders WHERE user_id = ? AND delivered = 0 ORDER BY remind_at ASC",
            (user_id,),
        )


def get_pending_reminders_for_user(user_id: int) -> list[dict]:
    """Return undelivered reminders for a user."""
    return get_reminders_for_user(user_id, include_delivered=False)


def get_due_reminders() -> list[dict]:
    """Return all undelivered reminders whose remind_at is in the past (UTC comparison)."""
    now = datetime.now(timezone.utc)
    with _get_conn() as conn:
        rows = _rows(
            conn,
            "SELECT * FROM reminders WHERE delivered = 0 ORDER BY remind_at ASC",
            (),
        )
    result = []
    for r in rows:
        try:
            remind_at_str = r["remind_at"]
            if "T" in remind_at_str and "+" in remind_at_str:
                remind_at = datetime.fromisoformat(remind_at_str.replace("Z", "+00:00"))
            elif "T" in remind_at_str:
                remind_at = datetime.fromisoformat(remind_at_str).replace(tzinfo=timezone.utc)
            else:
                remind_at = datetime.fromisoformat(remind_at_str).replace(tzinfo=timezone.utc)
            if remind_at <= now:
                result.append(r)
        except (ValueError, TypeError):
            continue
    return result


def mark_reminder_delivered(reminder_id: int) -> bool:
    """Mark a reminder as delivered. Returns True if updated."""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE reminders SET delivered = 1 WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_reminder(reminder_id: int, user_id: int) -> bool:
    """Delete a reminder. Enforces ownership. Returns True if deleted."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
