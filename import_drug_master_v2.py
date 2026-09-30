"""y_YYYYMMDD.csv (令和7年3月以降形式、厚労省薬品マスタ) を SQLite に取り込む。

y_ALL* とは列位置が 1つずれた新フォーマット。使い方:
  python import_drug_master_v2.py y_20260317.csv

新形式 列レイアウト (0-indexed, 42カラム):
  0:  "0"
  1:  "Y"
  2:  レセ電コード
  4:  薬品名
  6:  カナ名
  7:  剤形コード (16=錠 33=散 36=内服液 15=カプセル 6=貼付 10=坐剤 等)
  9:  単位 (錠/g/mL/包/枚 等)
  11: 薬価 (円/単位、令和7年4月改定)
  21: 用法区分 (0=適用外/1=内用/3=外用/7=注射/8=歯科用 等)
  27: 一般名フラグ
  29: valid_from (YYYYMMDD)
  30: valid_to
  31: YJコード (2325003B2029)
  36: 一般名YJ (2325003B2ZZZ)
  37: 一般名 (【般】ファモチジン散2%)
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

    # form_code / usage_kbn_cd カラム 追加 (未存在なら)
    dm_cols = [r[1] for r in conn.execute("PRAGMA table_info(drug_master)").fetchall()]
    if "form_code" not in dm_cols:
        conn.execute("ALTER TABLE drug_master ADD COLUMN form_code TEXT")
    if "yj_generic_code" not in dm_cols:
        conn.execute("ALTER TABLE drug_master ADD COLUMN yj_generic_code TEXT")

    # 変換: cp932 か utf8
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
            if len(row) < 32: continue
            yj_code = row[31].strip()
            if not yj_code: continue
            rece_code = row[2] or None
            name = row[4] or None
            name_kana = row[6] if len(row) > 6 else None
            form_code = row[7] if len(row) > 7 else None
            unit = row[9] if len(row) > 9 else None
            try:
                unit_price = float(row[11])
            except (ValueError, TypeError):
                unit_price = None
            usage_category = row[21] if len(row) > 21 else None
            valid_from = row[29] if len(row) > 29 else None
            valid_to = row[30] if len(row) > 30 else None
            yj_generic = row[36] if len(row) > 36 else None
            generic_name = row[37] if len(row) > 37 else None

            conn.execute(
                """INSERT OR REPLACE INTO drug_master
                   (yj_code, rece_code, name, name_kana, unit, unit_price,
                    usage_category, form_code, yj_generic_code,
                    generic_code, generic_name, valid_from, valid_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (yj_code, rece_code, name, name_kana, unit, unit_price,
                 usage_category, form_code, yj_generic,
                 yj_generic, generic_name, valid_from, valid_to),
            )
            inserted += 1

    conn.commit()
    return inserted


def main() -> None:
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else "y_master.csv")
    if not csv_path.exists():
        print(f"[ERROR] {csv_path} not found"); sys.exit(1)
    n = import_master(csv_path, Path(DB_PATH))
    print(f"imported {n} rows into drug_master")


if __name__ == "__main__":
    main()
