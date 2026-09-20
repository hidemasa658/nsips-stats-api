"""fee_master.csv (厚労省マスタ) を SQLite の fee_master テーブルに取り込む。

使い方: python import_fee_master.py fee_master.csv
CSV 想定: UTF-8 変換済 ("" で囲まれたカンマ区切り、232 行程度)
列レイアウト (0-indexed):
  0: "0"
  1: 種別記号 (M 等)
  2: 加算コード
  3: 分類コード
  4: 加算名
  5: (未使用)
  6: カナ名
  ...
  11: 点数 × 100
  ...
  61: valid_from (YYYYMMDD)
  62: valid_to (YYYYMMDD)
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

    # 厚労省マスタ CSV は Shift-JIS (cp932) が標準。UTF-8 も後方互換で試行
    try:
        with csv_path.open("r", encoding="cp932") as _test:
            _test.read(1024)
        encoding = "cp932"
    except UnicodeDecodeError:
        encoding = "utf-8"

    inserted = 0
    with csv_path.open("r", encoding=encoding) as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 12:
                continue
            code = row[2]
            name = row[4]
            name_kana = row[6] if len(row) > 6 else None
            category_code = row[3]
            try:
                points_raw = int(row[11])
                points = points_raw // 100  # マスタは 100倍値
            except (ValueError, TypeError):
                points = None
            valid_from = row[61] if len(row) > 61 else None
            valid_to = row[62] if len(row) > 62 else None

            conn.execute(
                """INSERT OR REPLACE INTO fee_master
                   (code, name, name_kana, points, category_code, valid_from, valid_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (code, name, name_kana, points, category_code, valid_from, valid_to),
            )
            inserted += 1

    conn.commit()
    return inserted


def main() -> None:
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else "fee_master.csv")
    if not csv_path.exists():
        print(f"[ERROR] {csv_path} not found")
        sys.exit(1)
    n = import_master(csv_path, Path(DB_PATH))
    print(f"imported {n} rows into fee_master")


if __name__ == "__main__":
    main()
