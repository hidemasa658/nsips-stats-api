"""SQLite 接続とスキーマ初期化。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prescriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT UNIQUE NOT NULL,
  detected_at TEXT NOT NULL,
  clinic_code_enc TEXT,
  clinic_name_enc TEXT,
  prescription_date_enc TEXT,
  doctor_name_enc TEXT
);

CREATE TABLE IF NOT EXISTS drugs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prescription_id INTEGER NOT NULL,
  rp_no_enc TEXT,
  yj_code TEXT,
  name TEXT,
  quantity REAL,
  unit TEXT,
  FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
);

CREATE INDEX IF NOT EXISTS idx_drugs_yj ON drugs(yj_code);

CREATE TABLE IF NOT EXISTS fees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prescription_id INTEGER NOT NULL,
  fee_type TEXT,
  code_enc TEXT,
  name_enc TEXT,
  count INTEGER,
  points INTEGER,
  is_mix_flag INTEGER DEFAULT 0,
  FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
);

CREATE INDEX IF NOT EXISTS idx_fees_mix ON fees(is_mix_flag);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """SQLite 接続を返す。親ディレクトリが無ければ作成、WAL モード有効化。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """スキーマを IF NOT EXISTS で流し込む (冪等)。"""
    conn.executescript(SCHEMA)
    conn.commit()
