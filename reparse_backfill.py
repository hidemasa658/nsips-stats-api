"""既存 prescriptions.body_sanitized を最新パーサーで再解析し、関連テーブルを更新する。

drugs / fees / rps / drug_pricings は DELETE + INSERT で総取っ替え。
prescriptions の totals フィールドは UPDATE。
暗号化フィールド (clinic_*_enc 等) と source_id, detected_at, body_sanitized は保持。

使い方: python reparse_backfill.py
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from db import connect, init_db
from nsips_parser import parse_nsips

load_dotenv()
DB_PATH = os.environ.get("DB_PATH", "./stats.db")


def reparse_all(db_path: Path) -> tuple[int, int]:
    conn = connect(db_path)
    init_db(conn)

    rows = conn.execute(
        "SELECT id, body_sanitized FROM prescriptions WHERE body_sanitized IS NOT NULL AND LENGTH(body_sanitized) > 0"
    ).fetchall()

    total = len(rows)
    updated = 0

    for r in rows:
        pid = r["id"]
        body = r["body_sanitized"]
        parsed = parse_nsips(body)

        # 既存の関連レコード削除
        conn.execute("DELETE FROM drugs WHERE prescription_id = ?", (pid,))
        conn.execute("DELETE FROM fees WHERE prescription_id = ?", (pid,))
        conn.execute("DELETE FROM rps WHERE prescription_id = ?", (pid,))
        conn.execute("DELETE FROM drug_pricings WHERE prescription_id = ?", (pid,))

        # 再挿入
        for d in parsed["drugs"]:
            conn.execute(
                """INSERT INTO drugs
                   (prescription_id, rp_no, yj_code, name, quantity, total_quantity,
                    unit_price, unit, form, dosage_form_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, d.get("rp_no"), d.get("yj_code"), d.get("name"),
                 d.get("quantity"), d.get("total_quantity"),
                 d.get("unit_price"), d.get("unit"),
                 d.get("form"), d.get("dosage_form_code")),
            )

        for f in parsed["fees"]:
            conn.execute(
                """INSERT INTO fees
                   (prescription_id, fee_type, code, name, count, points, is_mix_flag)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, f.get("fee_type"), f.get("code"), f.get("name"),
                 f.get("count"), f.get("points"),
                 1 if "計量混合" in (f.get("name") or "") else 0),
            )

        for rp in parsed["rps"]:
            conn.execute(
                """INSERT INTO rps
                   (prescription_id, rp_no, usage_code, usage_text, site_text,
                    is_mixed, drug_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, rp.get("rp_no"), rp.get("usage_code"), rp.get("usage_text"),
                 rp.get("site_text"), 1 if rp.get("is_mixed") else 0,
                 rp.get("drug_count", 0)),
            )

        for dp in parsed["drug_pricings"]:
            conn.execute(
                """INSERT INTO drug_pricings
                   (prescription_id, seq, dispensing_fee, drug_fee_per_unit,
                    quantity, total, internal_dispensing_fee)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, dp.get("seq"), dp.get("dispensing_fee"),
                 dp.get("drug_fee_per_unit"), dp.get("quantity"),
                 dp.get("total"), dp.get("internal_dispensing_fee")),
            )

        # prescriptions の totals を UPDATE
        t = parsed.get("totals", {})
        if t:
            conn.execute(
                """UPDATE prescriptions SET
                     total_points = ?,
                     dispensing_base_fee = ?,
                     night_holiday_fee = ?,
                     management_fee = ?,
                     long_prescription_fee = ?,
                     patient_copay = ?
                   WHERE id = ?""",
                (t.get("total_points"), t.get("dispensing_base_fee"),
                 t.get("night_holiday_fee"), t.get("management_fee"),
                 t.get("long_prescription_fee"), t.get("patient_copay"), pid),
            )

        updated += 1
        if updated % 20 == 0:
            print(f"  progress: {updated}/{total}")
            conn.commit()

    conn.commit()
    return total, updated


def main() -> None:
    total, updated = reparse_all(Path(DB_PATH))
    print(f"\nDone. re-parsed {updated}/{total} prescriptions.")


if __name__ == "__main__":
    main()
