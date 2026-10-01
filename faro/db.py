"""SQLite por campaña. Solo seudónimos en `accounts.pseudo`; el handle real va en la tabla
`identities`, que se puede cifrar/borrar sin romper el análisis."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (        -- SENSIBLE: mapa seudónimo -> handle real
  pseudo TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  handle TEXT NOT NULL,
  first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  pseudo TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  kind TEXT,                    -- channel | group | user | page | hashtag
  created_at TEXT,              -- fecha de creación de la cuenta si la plataforma la da
  followers INTEGER,
  title_hash TEXT,              -- sha256 del título, para detectar clones sin guardar el título
  seed INTEGER DEFAULT 0,       -- 1 si vino de la lista semilla
  notes TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,          -- <platform>:<native_id>
  platform TEXT NOT NULL,
  account TEXT NOT NULL REFERENCES accounts(pseudo),
  posted_at TEXT NOT NULL,      -- UTC ISO
  captured_at TEXT NOT NULL,
  text TEXT,
  text_norm TEXT,
  lang TEXT,
  url TEXT,
  views INTEGER,
  forwards INTEGER,
  replies INTEGER,
  fwd_from TEXT,                -- pseudo de la cuenta origen si es forward
  raw_path TEXT,
  raw_sha256 TEXT
);
CREATE INDEX IF NOT EXISTS ix_posts_time ON posts(posted_at);
CREATE INDEX IF NOT EXISTS ix_posts_account ON posts(account);
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT NOT NULL REFERENCES posts(id),
  kind TEXT,                    -- video | image | audio | doc
  path TEXT,
  sha256 TEXT,
  phash TEXT,                   -- perceptual hash (imagen o frame clave)
  duration REAL
);
CREATE INDEX IF NOT EXISTS ix_media_phash ON media(phash);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT NOT NULL REFERENCES posts(id),
  url TEXT NOT NULL,
  domain TEXT
);
CREATE INDEX IF NOT EXISTS ix_links_domain ON links(domain);
CREATE TABLE IF NOT EXISTS domains (
  domain TEXT PRIMARY KEY,
  registrar TEXT,
  created TEXT,
  registrant_country TEXT,
  ns TEXT,
  ip TEXT,
  asn TEXT,
  first_seen TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT NOT NULL,            -- pseudo
  dst TEXT NOT NULL,            -- pseudo
  kind TEXT NOT NULL,           -- forward | mention | dup_text | dup_media
  weight INTEGER DEFAULT 1,
  first_seen TEXT,
  PRIMARY KEY (src, dst, kind)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT, finished TEXT, platform TEXT, target TEXT, n_new INTEGER, status TEXT, error TEXT
);
"""


def db_path(data_dir: Path) -> Path:
    return data_dir / "faro.db"


@contextmanager
def connect(data_dir: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db_path(data_dir))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert_account(con: sqlite3.Connection, pseudo: str, platform: str, handle: str, now: str, **fields) -> None:
    con.execute(
        "INSERT OR IGNORE INTO identities(pseudo, platform, handle, first_seen) VALUES (?,?,?,?)",
        (pseudo, platform, handle, now),
    )
    con.execute("INSERT OR IGNORE INTO accounts(pseudo, platform) VALUES (?,?)", (pseudo, platform))
    for k, v in fields.items():
        if v is not None:
            con.execute(f"UPDATE accounts SET {k}=? WHERE pseudo=?", (v, pseudo))


def add_edge(con: sqlite3.Connection, src: str, dst: str, kind: str, now: str) -> None:
    con.execute(
        """INSERT INTO edges(src,dst,kind,weight,first_seen) VALUES (?,?,?,1,?)
           ON CONFLICT(src,dst,kind) DO UPDATE SET weight=weight+1""",
        (src, dst, kind, now),
    )
