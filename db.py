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
    # マイグレーション: fee_code / fee_name を平文で追加 (加算集計用)
    fees_cols = [r[1] for r in conn.execute("PRAGMA table_info(fees)").fetchall()]
    if "code" not in fees_cols:
        conn.execute("ALTER TABLE fees ADD COLUMN code TEXT")
    if "name" not in fees_cols:
        conn.execute("ALTER TABLE fees ADD COLUMN name TEXT")
    # マイグレーション: prescriptions に生データ (record 1 除去済) 追加
    presc_cols = [r[1] for r in conn.execute("PRAGMA table_info(prescriptions)").fetchall()]
    if "body_sanitized" not in presc_cols:
        conn.execute("ALTER TABLE prescriptions ADD COLUMN body_sanitized TEXT")
    # record 5 全体集計フィールド
    for col in ("total_points", "dispensing_base_fee", "night_holiday_fee",
                "management_fee", "long_prescription_fee", "patient_copay"):
        if col not in presc_cols:
            conn.execute(f"ALTER TABLE prescriptions ADD COLUMN {col} INTEGER")
    # drug_pricings に internal_dispensing_fee (record 6 末尾)
    dp_cols = [r[1] for r in conn.execute("PRAGMA table_info(drug_pricings)").fetchall()]
    if dp_cols and "internal_dispensing_fee" not in dp_cols:
        conn.execute("ALTER TABLE drug_pricings ADD COLUMN internal_dispensing_fee INTEGER")
    # drug_master に usage_category (col 27)
    dm_cols = [r[1] for r in conn.execute("PRAGMA table_info(drug_master)").fetchall()]
    if dm_cols and "usage_category" not in dm_cols:
        conn.execute("ALTER TABLE drug_master ADD COLUMN usage_category TEXT")
    # drugs: form 分類 + rp_no 平文
    drugs_cols = [r[1] for r in conn.execute("PRAGMA table_info(drugs)").fetchall()]
    if "form" not in drugs_cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN form TEXT")
    if "dosage_form_code" not in drugs_cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN dosage_form_code TEXT")
    if "rp_no" not in drugs_cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN rp_no TEXT")
    if "unit_price" not in drugs_cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN unit_price REAL")
    if "total_quantity" not in drugs_cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN total_quantity REAL")
    # rps テーブル (RP = 用法単位のグルーピング、混合検出用)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          prescription_id INTEGER NOT NULL,
          rp_no TEXT,
          usage_code TEXT,
          usage_text TEXT,
          site_text TEXT,
          is_mixed INTEGER DEFAULT 0,
          drug_count INTEGER DEFAULT 0,
          FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_rps_mixed ON rps(is_mixed);

        -- 加算・料金 マスタ (厚労省 m*.csv から取り込み)
        CREATE TABLE IF NOT EXISTS fee_master (
          code TEXT PRIMARY KEY,
          name TEXT,
          name_kana TEXT,
          points INTEGER,      -- CSV col 11 (実点数 = points/100)
          category_code TEXT,  -- CSV col 3
          valid_from TEXT,     -- CSV col 61
          valid_to TEXT        -- CSV col 62
        );

        -- 薬品マスタ (厚労省 y_ALL*.csv から取り込み)
        CREATE TABLE IF NOT EXISTS drug_master (
          yj_code TEXT PRIMARY KEY,
          rece_code TEXT,      -- レセ電コード (col 2)
          name TEXT,           -- 薬品名 (col 4)
          name_kana TEXT,      -- カナ名 (col 6)
          unit TEXT,           -- 単位 (col 9)
          unit_price REAL,     -- 薬価 (col 11)
          usage_category TEXT, -- 用法区分 (col 27): 1=内用 4=注射 6=外用
          generic_code TEXT,   -- 一般名コード (col 37)
          generic_name TEXT,   -- 一般名 (col 38)
          valid_from TEXT,
          valid_to TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_drug_master_rece ON drug_master(rece_code);
        CREATE INDEX IF NOT EXISTS idx_drug_master_generic ON drug_master(generic_code);

        -- record 6 基本料 (剤ごとの 調剤料 + 薬剤料単価 × 数量 = 合計)
        CREATE TABLE IF NOT EXISTS drug_pricings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          prescription_id INTEGER NOT NULL,
          seq TEXT,
          dispensing_fee INTEGER,
          drug_fee_per_unit INTEGER,
          quantity INTEGER,
          total INTEGER,
          internal_dispensing_fee INTEGER,
          FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        );
        """
    )
    conn.commit()
