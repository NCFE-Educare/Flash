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

            -- Kanban Board System Tables

            CREATE TABLE IF NOT EXISTS workspaces (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL,
                description      TEXT,
                owner_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS workspace_members (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role             TEXT NOT NULL CHECK(role IN ('owner', 'member')) DEFAULT 'member',
                added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workspace_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS workspace_invitations (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                email            TEXT NOT NULL,
                invited_by       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token            TEXT UNIQUE NOT NULL,
                status           TEXT CHECK(status IN ('pending', 'accepted', 'declined')) DEFAULT 'pending',
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at       TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS board_columns (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                name             TEXT NOT NULL,
                position         INTEGER NOT NULL DEFAULT 0,
                color            TEXT DEFAULT '#808080',
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                column_id        INTEGER NOT NULL REFERENCES board_columns(id) ON DELETE CASCADE,
                title            TEXT NOT NULL,
                description      TEXT,
                assignee_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reporter_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                priority         TEXT CHECK(priority IN ('low', 'medium', 'high', 'urgent')) DEFAULT 'medium',
                due_date         TIMESTAMP,
                position         INTEGER NOT NULL DEFAULT 0,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS task_comments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                comment          TEXT NOT NULL,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS task_attachments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                file_name        TEXT NOT NULL,
                file_url         TEXT NOT NULL,
                file_type        TEXT,
                file_size        INTEGER,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                task_id          INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action           TEXT NOT NULL,
                description      TEXT NOT NULL,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------

def create_workspace(name: str, description: str | None, owner_id: int) -> dict:
    """Create a new workspace and add default columns."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO workspaces (name, description, owner_id) VALUES (?, ?, ?)",
            (name, description, owner_id),
        )
        workspace_id = cur.lastrowid

        # Add owner as member
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (?, ?, ?)",
            (workspace_id, owner_id, "owner"),
        )

        # Create default columns
        default_columns = [
            ("To Do", 0, "#6B7280"),
            ("In Progress", 1, "#3B82F6"),
            ("Review", 2, "#F59E0B"),
            ("Done", 3, "#10B981"),
        ]
        for col_name, position, color in default_columns:
            conn.execute(
                "INSERT INTO board_columns (workspace_id, name, position, color) VALUES (?, ?, ?, ?)",
                (workspace_id, col_name, position, color),
            )

        # Log activity
        conn.execute(
            "INSERT INTO activity_log (workspace_id, user_id, action, description) VALUES (?, ?, ?, ?)",
            (workspace_id, owner_id, "workspace_created", f"Created workspace '{name}'"),
        )

        conn.commit()
        return _row(conn, "SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


def get_workspace(workspace_id: int) -> dict | None:
    """Get workspace details."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


def get_user_workspaces(user_id: int) -> list[dict]:
    """Get all workspaces the user is a member of."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT w.* FROM workspaces w
            JOIN workspace_members wm ON w.id = wm.workspace_id
            WHERE wm.user_id = ?
            ORDER BY w.updated_at DESC
            """,
            (user_id,),
        )


def update_workspace(workspace_id: int, name: str | None, description: str | None) -> dict | None:
    """Update workspace details."""
    with _get_conn() as conn:
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return get_workspace(workspace_id)

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(workspace_id)

        conn.execute(
            f"UPDATE workspaces SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return _row(conn, "SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


def delete_workspace(workspace_id: int) -> bool:
    """Delete a workspace and all its data (cascades to members, tasks, etc.)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        conn.commit()
        return cur.rowcount > 0


def is_workspace_member(workspace_id: int, user_id: int) -> bool:
    """Check if user is a member of workspace."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
        return row is not None


def is_workspace_owner(workspace_id: int, user_id: int) -> bool:
    """Check if user is the owner of workspace."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ? AND role = 'owner'",
            (workspace_id, user_id),
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# Workspace Members
# ---------------------------------------------------------------------------

def get_workspace_members(workspace_id: int) -> list[dict]:
    """Get all members of a workspace with user details."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT wm.*, u.email, u.username
            FROM workspace_members wm
            JOIN users u ON wm.user_id = u.id
            WHERE wm.workspace_id = ?
            ORDER BY wm.added_at ASC
            """,
            (workspace_id,),
        )


def add_workspace_member(workspace_id: int, user_id: int) -> dict | None:
    """Add a user to workspace as member."""
    with _get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (?, ?, ?)",
                (workspace_id, user_id, "member"),
            )
            conn.commit()
            return _row(conn, "SELECT * FROM workspace_members WHERE id = ?", (cur.lastrowid,))
        except sqlite3.IntegrityError:
            return None  # Already a member


def remove_workspace_member(workspace_id: int, user_id: int) -> bool:
    """Remove a member from workspace (cannot remove owner)."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ? AND role != 'owner'",
            (workspace_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Workspace Invitations
# ---------------------------------------------------------------------------

def create_workspace_invitation(workspace_id: int, email: str, invited_by: int, token: str, expires_at: str) -> dict:
    """Create a pending invitation."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO workspace_invitations (workspace_id, email, invited_by, token, expires_at) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, email, invited_by, token, expires_at),
        )
        conn.commit()
        return _row(conn, "SELECT * FROM workspace_invitations WHERE id = ?", (cur.lastrowid,))


def get_invitation_by_token(token: str) -> dict | None:
    """Get invitation by token."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM workspace_invitations WHERE token = ?", (token,))


def accept_invitation(invitation_id: int, user_id: int) -> bool:
    """Accept an invitation and add user to workspace."""
    with _get_conn() as conn:
        invitation = _row(conn, "SELECT * FROM workspace_invitations WHERE id = ?", (invitation_id,))
        if not invitation or invitation["status"] != "pending":
            return False

        # Add user to workspace
        try:
            conn.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (?, ?, ?)",
                (invitation["workspace_id"], user_id, "member"),
            )
        except sqlite3.IntegrityError:
            pass  # Already a member

        # Mark invitation as accepted
        conn.execute(
            "UPDATE workspace_invitations SET status = 'accepted' WHERE id = ?",
            (invitation_id,),
        )

        # Log activity
        workspace = get_workspace(invitation["workspace_id"])
        conn.execute(
            "INSERT INTO activity_log (workspace_id, user_id, action, description) VALUES (?, ?, ?, ?)",
            (invitation["workspace_id"], user_id, "member_joined", f"Joined workspace '{workspace['name']}'"),
        )

        conn.commit()
        return True


def decline_invitation(invitation_id: int) -> bool:
    """Decline an invitation."""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE workspace_invitations SET status = 'declined' WHERE id = ? AND status = 'pending'",
            (invitation_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def get_pending_invitations_by_email(email: str) -> list[dict]:
    """Get all pending invitations for an email."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT wi.*, w.name as workspace_name, u.username as inviter_name
            FROM workspace_invitations wi
            JOIN workspaces w ON wi.workspace_id = w.id
            JOIN users u ON wi.invited_by = u.id
            WHERE wi.email = ? AND wi.status = 'pending'
            ORDER BY wi.created_at DESC
            """,
            (email,),
        )


# ---------------------------------------------------------------------------
# Board Columns
# ---------------------------------------------------------------------------

def get_workspace_columns(workspace_id: int) -> list[dict]:
    """Get all columns for a workspace."""
    with _get_conn() as conn:
        return _rows(
            conn,
            "SELECT * FROM board_columns WHERE workspace_id = ? ORDER BY position ASC",
            (workspace_id,),
        )


def create_column(workspace_id: int, name: str, position: int, color: str = "#808080") -> dict:
    """Create a new column."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO board_columns (workspace_id, name, position, color) VALUES (?, ?, ?, ?)",
            (workspace_id, name, position, color),
        )
        conn.commit()
        return _row(conn, "SELECT * FROM board_columns WHERE id = ?", (cur.lastrowid,))


def update_column(column_id: int, name: str | None, position: int | None, color: str | None) -> dict | None:
    """Update column details."""
    with _get_conn() as conn:
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if position is not None:
            updates.append("position = ?")
            params.append(position)
        if color is not None:
            updates.append("color = ?")
            params.append(color)

        if not updates:
            return _row(conn, "SELECT * FROM board_columns WHERE id = ?", (column_id,))

        params.append(column_id)
        conn.execute(
            f"UPDATE board_columns SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return _row(conn, "SELECT * FROM board_columns WHERE id = ?", (column_id,))


def delete_column(column_id: int) -> bool:
    """Delete a column (will fail if tasks exist in it due to foreign key)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM board_columns WHERE id = ?", (column_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(
    workspace_id: int,
    column_id: int,
    title: str,
    description: str | None,
    reporter_id: int,
    assignee_id: int | None = None,
    priority: str = "medium",
    due_date: str | None = None,
    position: int = 0,
) -> dict:
    """Create a new task."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (workspace_id, column_id, title, description, reporter_id, assignee_id, priority, due_date, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, column_id, title, description, reporter_id, assignee_id, priority, due_date, position),
        )
        task_id = cur.lastrowid

        # Update workspace timestamp
        conn.execute(
            "UPDATE workspaces SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (workspace_id,),
        )

        # Log activity
        assignee_name = ""
        if assignee_id:
            assignee = _row(conn, "SELECT username FROM users WHERE id = ?", (assignee_id,))
            assignee_name = f" to {assignee['username']}" if assignee else ""

        conn.execute(
            "INSERT INTO activity_log (workspace_id, task_id, user_id, action, description) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, task_id, reporter_id, "task_created", f"Created task '{title}'{assignee_name}"),
        )

        conn.commit()
        return _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))


def get_task(task_id: int) -> dict | None:
    """Get task details."""
    with _get_conn() as conn:
        return _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))


def get_workspace_tasks(workspace_id: int) -> list[dict]:
    """Get all tasks in a workspace with user details."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT t.*,
                   r.username as reporter_name, r.email as reporter_email,
                   a.username as assignee_name, a.email as assignee_email,
                   c.name as column_name, c.color as column_color
            FROM tasks t
            JOIN users r ON t.reporter_id = r.id
            LEFT JOIN users a ON t.assignee_id = a.id
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.workspace_id = ?
            ORDER BY c.position ASC, t.position ASC
            """,
            (workspace_id,),
        )


def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    column_id: int | None = None,
    assignee_id: int | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    position: int | None = None,
    user_id: int | None = None,
) -> dict | None:
    """Update task details and log activity."""
    with _get_conn() as conn:
        task = _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return None

        updates = []
        params = []
        activities = []

        if title is not None and title != task["title"]:
            updates.append("title = ?")
            params.append(title)
            activities.append(f"Updated title to '{title}'")

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if column_id is not None and column_id != task["column_id"]:
            updates.append("column_id = ?")
            params.append(column_id)
            old_col = _row(conn, "SELECT name FROM board_columns WHERE id = ?", (task["column_id"],))
            new_col = _row(conn, "SELECT name FROM board_columns WHERE id = ?", (column_id,))
            if old_col and new_col:
                activities.append(f"Moved from '{old_col['name']}' to '{new_col['name']}'")

        if assignee_id is not None and assignee_id != task["assignee_id"]:
            updates.append("assignee_id = ?")
            params.append(assignee_id)
            assignee = _row(conn, "SELECT username FROM users WHERE id = ?", (assignee_id,)) if assignee_id else None
            if assignee:
                activities.append(f"Assigned to {assignee['username']}")
            else:
                activities.append("Unassigned")

        if priority is not None and priority != task["priority"]:
            updates.append("priority = ?")
            params.append(priority)
            activities.append(f"Changed priority to {priority}")

        if due_date is not None:
            updates.append("due_date = ?")
            params.append(due_date)

        if position is not None:
            updates.append("position = ?")
            params.append(position)

        if not updates:
            return task

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(task_id)

        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            params,
        )

        # Log activities
        if user_id and activities:
            for activity in activities:
                conn.execute(
                    "INSERT INTO activity_log (workspace_id, task_id, user_id, action, description) VALUES (?, ?, ?, ?, ?)",
                    (task["workspace_id"], task_id, user_id, "task_updated", activity),
                )

        # Update workspace timestamp
        conn.execute(
            "UPDATE workspaces SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (task["workspace_id"],),
        )

        conn.commit()
        return _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))


def delete_task(task_id: int, user_id: int | None = None) -> bool:
    """Delete a task and log activity."""
    with _get_conn() as conn:
        task = _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return False

        if user_id:
            conn.execute(
                "INSERT INTO activity_log (workspace_id, task_id, user_id, action, description) VALUES (?, ?, ?, ?, ?)",
                (task["workspace_id"], task_id, user_id, "task_deleted", f"Deleted task '{task['title']}'"),
            )

        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Task Comments
# ---------------------------------------------------------------------------

def create_task_comment(task_id: int, user_id: int, comment: str) -> dict:
    """Add a comment to a task."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO task_comments (task_id, user_id, comment) VALUES (?, ?, ?)",
            (task_id, user_id, comment),
        )
        comment_id = cur.lastrowid

        # Log activity
        task = _row(conn, "SELECT workspace_id FROM tasks WHERE id = ?", (task_id,))
        if task:
            conn.execute(
                "INSERT INTO activity_log (workspace_id, task_id, user_id, action, description) VALUES (?, ?, ?, ?, ?)",
                (task["workspace_id"], task_id, user_id, "comment_added", "Added a comment"),
            )

        conn.commit()
        return _row(conn, "SELECT * FROM task_comments WHERE id = ?", (comment_id,))


def get_task_comments(task_id: int) -> list[dict]:
    """Get all comments for a task with user details."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT tc.*, u.username, u.email
            FROM task_comments tc
            JOIN users u ON tc.user_id = u.id
            WHERE tc.task_id = ?
            ORDER BY tc.created_at ASC
            """,
            (task_id,),
        )


def delete_task_comment(comment_id: int, user_id: int) -> bool:
    """Delete a comment (only by the user who created it)."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM task_comments WHERE id = ? AND user_id = ?",
            (comment_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Task Attachments
# ---------------------------------------------------------------------------

def create_task_attachment(task_id: int, user_id: int, file_name: str, file_url: str, file_type: str | None, file_size: int | None) -> dict:
    """Add an attachment to a task."""
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO task_attachments (task_id, user_id, file_name, file_url, file_type, file_size) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, user_id, file_name, file_url, file_type, file_size),
        )
        attachment_id = cur.lastrowid

        # Log activity
        task = _row(conn, "SELECT workspace_id FROM tasks WHERE id = ?", (task_id,))
        if task:
            conn.execute(
                "INSERT INTO activity_log (workspace_id, task_id, user_id, action, description) VALUES (?, ?, ?, ?, ?)",
                (task["workspace_id"], task_id, user_id, "attachment_added", f"Uploaded file '{file_name}'"),
            )

        conn.commit()
        return _row(conn, "SELECT * FROM task_attachments WHERE id = ?", (attachment_id,))


def get_task_attachments(task_id: int) -> list[dict]:
    """Get all attachments for a task."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT ta.*, u.username
            FROM task_attachments ta
            JOIN users u ON ta.user_id = u.id
            WHERE ta.task_id = ?
            ORDER BY ta.created_at DESC
            """,
            (task_id,),
        )


def delete_task_attachment(attachment_id: int, user_id: int) -> bool:
    """Delete an attachment (only by the user who uploaded it)."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM task_attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Activity Log
# ---------------------------------------------------------------------------

def get_workspace_activity(workspace_id: int, limit: int = 50) -> list[dict]:
    """Get recent activity for a workspace."""
    with _get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT al.*, u.username, u.email
            FROM activity_log al
            JOIN users u ON al.user_id = u.id
            WHERE al.workspace_id = ?
            ORDER BY al.created_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        )


# ---------------------------------------------------------------------------
# Analytics & Dashboard
# ---------------------------------------------------------------------------

def get_workspace_analytics(workspace_id: int) -> dict:
    """Get comprehensive analytics for a workspace."""
    with _get_conn() as conn:
        # Task counts by column
        tasks_by_column = _rows(
            conn,
            """
            SELECT c.name as column_name, c.color, COUNT(t.id) as task_count
            FROM board_columns c
            LEFT JOIN tasks t ON c.id = t.column_id
            WHERE c.workspace_id = ?
            GROUP BY c.id, c.name, c.color
            ORDER BY c.position ASC
            """,
            (workspace_id,),
        )

        # Task counts by priority
        tasks_by_priority = _rows(
            conn,
            """
            SELECT priority, COUNT(*) as count
            FROM tasks
            WHERE workspace_id = ?
            GROUP BY priority
            """,
            (workspace_id,),
        )

        # Task counts by assignee
        tasks_by_assignee = _rows(
            conn,
            """
            SELECT u.username, u.email, COUNT(t.id) as task_count
            FROM users u
            JOIN tasks t ON u.id = t.assignee_id
            WHERE t.workspace_id = ?
            GROUP BY u.id, u.username, u.email
            ORDER BY task_count DESC
            """,
            (workspace_id,),
        )

        # Overdue tasks
        overdue_tasks = _rows(
            conn,
            """
            SELECT t.*, u.username as assignee_name
            FROM tasks t
            LEFT JOIN users u ON t.assignee_id = u.id
            WHERE t.workspace_id = ?
              AND t.due_date IS NOT NULL
              AND t.due_date < datetime('now')
              AND t.column_id NOT IN (
                  SELECT id FROM board_columns WHERE workspace_id = ? AND name = 'Done'
              )
            ORDER BY t.due_date ASC
            """,
            (workspace_id, workspace_id),
        )

        # Completion rate (tasks in Done column)
        completion_stats = _row(
            conn,
            """
            SELECT
                COUNT(*) as total_tasks,
                SUM(CASE WHEN c.name = 'Done' THEN 1 ELSE 0 END) as completed_tasks
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.workspace_id = ?
            """,
            (workspace_id,),
        )

        # Tasks created over time (last 30 days)
        tasks_over_time = _rows(
            conn,
            """
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM tasks
            WHERE workspace_id = ?
              AND created_at >= datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (workspace_id,),
        )

        # Member activity (task counts)
        member_activity = _rows(
            conn,
            """
            SELECT
                u.username,
                u.email,
                COUNT(DISTINCT t.id) as tasks_assigned,
                COUNT(DISTINCT tc.id) as comments_made,
                COUNT(DISTINCT ta.id) as files_uploaded
            FROM workspace_members wm
            JOIN users u ON wm.user_id = u.id
            LEFT JOIN tasks t ON u.id = t.assignee_id AND t.workspace_id = ?
            LEFT JOIN task_comments tc ON u.id = tc.user_id
            LEFT JOIN task_attachments ta ON u.id = ta.user_id
            WHERE wm.workspace_id = ?
            GROUP BY u.id, u.username, u.email
            ORDER BY tasks_assigned DESC
            """,
            (workspace_id, workspace_id),
        )

        total_tasks = completion_stats["total_tasks"] if completion_stats else 0
        completed_tasks = completion_stats["completed_tasks"] if completion_stats else 0
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        return {
            "tasks_by_column": tasks_by_column,
            "tasks_by_priority": tasks_by_priority,
            "tasks_by_assignee": tasks_by_assignee,
            "overdue_tasks": overdue_tasks,
            "completion_rate": round(completion_rate, 2),
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "tasks_over_time": tasks_over_time,
            "member_activity": member_activity,
        }


def get_user_analytics(user_id: int) -> dict:
    """Get analytics for a specific user across all their workspaces."""
    with _get_conn() as conn:
        # Tasks assigned to user
        my_tasks = _rows(
            conn,
            """
            SELECT t.*, w.name as workspace_name, c.name as column_name
            FROM tasks t
            JOIN workspaces w ON t.workspace_id = w.id
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ?
            ORDER BY t.due_date ASC NULLS LAST
            """,
            (user_id,),
        )

        # Task counts by status
        tasks_by_status = _rows(
            conn,
            """
            SELECT c.name as column_name, COUNT(t.id) as count
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ?
            GROUP BY c.name
            """,
            (user_id,),
        )

        # Overdue tasks
        overdue_count = _row(
            conn,
            """
            SELECT COUNT(*) as count
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ?
              AND t.due_date < datetime('now')
              AND c.name != 'Done'
            """,
            (user_id,),
        )

        # Tasks by workspace
        tasks_by_workspace = _rows(
            conn,
            """
            SELECT w.name as workspace_name, w.id as workspace_id, COUNT(t.id) as task_count
            FROM tasks t
            JOIN workspaces w ON t.workspace_id = w.id
            WHERE t.assignee_id = ?
            GROUP BY w.id, w.name
            ORDER BY task_count DESC
            """,
            (user_id,),
        )

        # Activity stats
        activity_stats = _row(
            conn,
            """
            SELECT
                COUNT(DISTINCT t.id) as tasks_created,
                COUNT(DISTINCT tc.id) as comments_made,
                COUNT(DISTINCT ta.id) as files_uploaded
            FROM users u
            LEFT JOIN tasks t ON u.id = t.reporter_id
            LEFT JOIN task_comments tc ON u.id = tc.user_id
            LEFT JOIN task_attachments ta ON u.id = ta.user_id
            WHERE u.id = ?
            """,
            (user_id,),
        )

        return {
            "my_tasks": my_tasks,
            "tasks_by_status": tasks_by_status,
            "overdue_count": overdue_count["count"] if overdue_count else 0,
            "tasks_by_workspace": tasks_by_workspace,
            "activity_stats": activity_stats or {},
        }


def get_global_analytics() -> dict:
    """Get system-wide analytics (admin view)."""
    with _get_conn() as conn:
        # Total counts
        totals = _row(
            conn,
            """
            SELECT
                (SELECT COUNT(*) FROM workspaces) as total_workspaces,
                (SELECT COUNT(*) FROM users) as total_users,
                (SELECT COUNT(*) FROM tasks) as total_tasks,
                (SELECT COUNT(*) FROM tasks WHERE column_id IN (SELECT id FROM board_columns WHERE name = 'Done')) as completed_tasks
            """,
            (),
        )

        # Most active workspaces
        active_workspaces = _rows(
            conn,
            """
            SELECT w.id, w.name, COUNT(t.id) as task_count, COUNT(DISTINCT wm.user_id) as member_count
            FROM workspaces w
            LEFT JOIN tasks t ON w.id = t.workspace_id
            LEFT JOIN workspace_members wm ON w.id = wm.workspace_id
            GROUP BY w.id, w.name
            ORDER BY task_count DESC
            LIMIT 10
            """,
            (),
        )

        # Most active users
        active_users = _rows(
            conn,
            """
            SELECT u.username, u.email,
                   COUNT(DISTINCT t.id) as tasks_assigned,
                   COUNT(DISTINCT tc.id) as comments_made
            FROM users u
            LEFT JOIN tasks t ON u.id = t.assignee_id
            LEFT JOIN task_comments tc ON u.id = tc.user_id
            GROUP BY u.id, u.username, u.email
            ORDER BY tasks_assigned DESC
            LIMIT 10
            """,
            (),
        )

        # Tasks created over time (last 30 days)
        tasks_trend = _rows(
            conn,
            """
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM tasks
            WHERE created_at >= datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (),
        )

        return {
            "totals": totals or {},
            "active_workspaces": active_workspaces,
            "active_users": active_users,
            "tasks_trend": tasks_trend,
        }
