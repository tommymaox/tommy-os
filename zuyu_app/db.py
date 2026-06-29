from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def db_session(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = get_db(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(settings: Settings) -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with db_session(settings.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                type TEXT DEFAULT 'event',
                start_time TEXT,
                end_time TEXT,
                notes TEXT,
                color TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brand TEXT,
                serving_size REAL DEFAULT 100,
                serving_unit TEXT DEFAULT 'g',
                kj REAL DEFAULT 0,
                protein REAL DEFAULT 0,
                carbs REAL DEFAULT 0,
                fat REAL DEFAULT 0,
                fibre REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_log (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                food_item_id TEXT,
                recipe_id TEXT,
                meal_slot TEXT NOT NULL DEFAULT 'snack',
                custom_name TEXT,
                servings REAL DEFAULT 1,
                kj_override REAL,
                protein_override REAL,
                carbs_override REAL,
                fat_override REAL,
                fibre_override REAL,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_recipes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                servings REAL DEFAULT 1,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_recipe_items (
                id TEXT PRIMARY KEY,
                recipe_id TEXT NOT NULL,
                food_item_id TEXT,
                custom_name TEXT,
                quantity REAL DEFAULT 1,
                kj_override REAL,
                protein_override REAL,
                carbs_override REAL,
                fat_override REAL,
                fibre_override REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        ensure_column(conn, "food_items", "notes", "TEXT")
        ensure_column(conn, "food_items", "ingredients", "TEXT")
        ensure_column(conn, "food_items", "steps", "TEXT")
        ensure_column(conn, "food_items", "photo", "TEXT")
        ensure_column(conn, "food_log", "recipe_id", "TEXT")
        ensure_column(conn, "food_log", "fibre_override", "REAL")
        ensure_column(conn, "food_log", "updated_at", "TEXT")
        conn.execute("UPDATE food_log SET updated_at = COALESCE(updated_at, created_at, datetime('now'))")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS briefings (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                content TEXT,
                error TEXT,
                sources_fetched INTEGER DEFAULT 0,
                generated_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_log_date ON food_log(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_log_food_item_id ON food_log(food_item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_log_recipe_id ON food_log(recipe_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_store_updated_at ON kb_store(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_briefings_date ON briefings(date)")

        # ── Wiki Sources & Ingestion Pipeline ──────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wiki_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                config_json TEXT DEFAULT '{}',
                last_sync_at TEXT,
                items_total INTEGER DEFAULT 0,
                items_processed INTEGER DEFAULT 0,
                health_score INTEGER DEFAULT 100,
                error_msg TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wiki_ingest_jobs (
                id TEXT PRIMARY KEY,
                source_id TEXT,
                source_name TEXT,
                status TEXT DEFAULT 'pending',
                items_found INTEGER DEFAULT 0,
                items_processed INTEGER DEFAULT 0,
                pages_created INTEGER DEFAULT 0,
                pages_updated INTEGER DEFAULT 0,
                error_msg TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_ingest_jobs_created ON wiki_ingest_jobs(created_at)")

        # Seed initial wiki sources if table is empty
        _src_count = conn.execute("SELECT COUNT(*) FROM wiki_sources").fetchone()[0]
        if _src_count == 0:
            import json as _json
            _demo_sources = [
                ("src-gmail", "Gmail — Work & Personal", "email", "active",
                 _json.dumps({"account": "zuyumao12@gmail.com", "labels": ["inbox", "sent", "career"]}),
                 "2026-04-22T08:15:00", 1847, 1203, 95, None),
                ("src-slack", "Slack — Team Workspace", "slack", "active",
                 _json.dumps({"workspace": "feifei-team", "channels": ["#general", "#projects"]}),
                 "2026-04-22T14:30:00", 4231, 2890, 98, None),
                ("src-drive", "Google Drive — Docs & Slides", "drive", "active",
                 _json.dumps({"folders": ["My Drive/Work", "My Drive/Learning"]}),
                 "2026-04-21T22:00:00", 312, 289, 92, None),
                ("src-excel", "Excel / CSV — Legacy Ops Data", "excel", "warning",
                 _json.dumps({"path": "/data/legacy/", "formats": ["xlsx", "csv"], "sheets": 14}),
                 "2026-04-20T10:45:00", 5620, 4100, 72, "3 files had encoding issues"),
                ("src-db", "Operations Database (SQLite)", "database", "active",
                 _json.dumps({"type": "sqlite", "tables": ["customers", "projects", "notes"]}),
                 "2026-04-23T06:00:00", 924, 924, 100, None),
                ("src-transcripts", "Meeting Transcripts — Zoom/Teams", "transcript", "active",
                 _json.dumps({"source": "otter.ai", "format": "txt", "auto_process": True}),
                 "2026-04-19T17:20:00", 87, 62, 88, None),
                ("src-notion", "Notion Export", "notion", "inactive",
                 _json.dumps({"workspace": "personal", "pages": 43}),
                 "2026-03-15T12:00:00", 43, 43, 100, None),
            ]
            for _src in _demo_sources:
                conn.execute(
                    """INSERT OR IGNORE INTO wiki_sources
                       (id, name, source_type, status, config_json, last_sync_at,
                        items_total, items_processed, health_score, error_msg)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    _src,
                )

        # Seed initial ingest jobs if table is empty
        _job_count = conn.execute("SELECT COUNT(*) FROM wiki_ingest_jobs").fetchone()[0]
        if _job_count == 0:
            _demo_jobs = [
                ("job-001", "src-db", "Operations Database (SQLite)", "done",
                 924, 924, 12, 3, None, "2026-04-23T06:00:00", "2026-04-23T06:04:22", "2026-04-23T06:00:00"),
                ("job-002", "src-gmail", "Gmail — Work & Personal", "done",
                 342, 341, 8, 5, None, "2026-04-22T08:00:00", "2026-04-22T08:15:00", "2026-04-22T08:00:00"),
                ("job-003", "src-slack", "Slack — Team Workspace", "done",
                 891, 889, 15, 7, None, "2026-04-22T14:00:00", "2026-04-22T14:30:00", "2026-04-22T14:00:00"),
                ("job-004", "src-excel", "Excel / CSV — Legacy Ops Data", "error",
                 1200, 1097, 4, 0, "3 files had encoding issues",
                 "2026-04-20T10:00:00", "2026-04-20T10:45:00", "2026-04-20T10:00:00"),
                ("job-005", "src-drive", "Google Drive — Docs & Slides", "done",
                 89, 87, 6, 2, None, "2026-04-21T22:00:00", "2026-04-21T22:00:00", "2026-04-21T22:00:00"),
                ("job-006", "src-transcripts", "Meeting Transcripts — Zoom/Teams", "done",
                 12, 12, 3, 0, None, "2026-04-19T17:00:00", "2026-04-19T17:20:00", "2026-04-19T17:00:00"),
                ("job-007", "src-notion", "Notion Export", "done",
                 43, 43, 5, 0, None, "2026-03-15T11:00:00", "2026-03-15T12:00:00", "2026-03-15T11:00:00"),
            ]
            for _job in _demo_jobs:
                conn.execute(
                    """INSERT OR IGNORE INTO wiki_ingest_jobs
                       (id, source_id, source_name, status, items_found, items_processed,
                        pages_created, pages_updated, error_msg, started_at, completed_at, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _job,
                )
