"""y_ALL*.csv (厚労省 薬品マスタ) を SQLite の drug_master テーブルに取り込む。

使い方: python import_drug_master.py y_ALL20260911_utf8.csv

CSV は UTF-8 変換済のもの (iconv -f CP932 -t UTF-8 y_ALL*.csv > y_ALL_utf8.csv)。
列レイアウト (0-indexed):
  0: "0"
  1: "Y"
  2: レセ電コード
  3: 分類
  4: 薬品名
  6: カナ名
  9: 単位
  11: 薬価 (円/単位)
  30: valid_from
  31: valid_to
  32: YJコード
  37: 一般名コード
  38: 一般名
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from db import connect, init_db

load_dotenv()
DB_PATH = os.environ.get("DB_PATH", "./stats.db")


def import_master(csv_path: Path, db_path: Path) -> int:
    conn = connect(db_path) if db_path.name != ":memory:" else sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    inserted = 0
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 33:
                continue
            yj_code = row[32].strip()
            if not yj_code:
                continue
            rece_code = row[2] or None
            name = row[4] or None
            name_kana = row[6] if len(row) > 6 else None
            unit = row[9] if len(row) > 9 else None
            try:
                unit_price = float(row[11])
            except (ValueError, TypeError):
                unit_price = None
            usage_category = row[27] if len(row) > 27 else None  # 1=内用 4=注射 6=外用
            valid_from = row[30] if len(row) > 30 else None
            valid_to = row[31] if len(row) > 31 else None
            generic_code = row[37] if len(row) > 37 else None
            generic_name = row[38] if len(row) > 38 else None

            conn.execute(
                """INSERT OR REPLACE INTO drug_master
                   (yj_code, rece_code, name, name_kana, unit, unit_price,
                    usage_category, generic_code, generic_name, valid_from, valid_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (yj_code, rece_code, name, name_kana, unit, unit_price,
                 usage_category, generic_code, generic_name, valid_from, valid_to),
            )
            inserted += 1

    conn.commit()
    return inserted


def main() -> None:
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else "drug_master.csv")
    if not csv_path.exists():
        print(f"[ERROR] {csv_path} not found")
        sys.exit(1)
    n = import_master(csv_path, Path(DB_PATH))
    print(f"imported {n} rows into drug_master")


if __name__ == "__main__":
    main()
