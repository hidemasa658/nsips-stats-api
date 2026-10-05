"""既存の D レコードと対応 A/U を一括削除する ワンタイムクリーンアップ。

ingest に D 処理が入る前に DB に残ってしまった以下を対象:
- D レコード自身 (値 NULL のゴミ)
- 同じ (dispense_date, 受付番号, 枝番) の対応 A/U

実行:
    python3 cleanup_d_records.py           # dry-run (削除対象を表示するだけ)
    python3 cleanup_d_records.py --apply   # 実削除
"""
from __future__ import annotations

import argparse
import sys

import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "/root/nsips-stats-api/stats.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_receipt_info(body_sanitized):
    if not body_sanitized:
        return None
    for line in body_sanitized.splitlines():
        parts = line.split(",")
        if parts and parts[0] == "2" and len(parts) >= 4:
            return parts[1], parts[2], parts[3]
    return None


def _delete_prescription_cascade(conn, pid):
    for table in ("drugs", "fees", "rps", "drug_pricings", "mix_events"):
        conn.execute(f"DELETE FROM {table} WHERE prescription_id=?", (pid,))
    conn.execute("DELETE FROM prescriptions WHERE id=?", (pid,))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="実削除する")
    args = parser.parse_args()

    conn = get_conn()
    # 全レコードから receipt_info を取り、D を見つける
    print("スキャン開始...")
    all_rows = conn.execute(
        "SELECT id, pharmacy_code, dispense_date, body_sanitized "
        "FROM prescriptions WHERE body_sanitized IS NOT NULL"
    ).fetchall()
    print(f"総レコード数: {len(all_rows)}")

    # D は 枝番空で届くため、キーを (薬局, dispense_date, 受付番号) にして
    # 全業務区分 (A/U/D) で グループ化する
    by_key: dict = {}
    for r in all_rows:
        info = _extract_receipt_info(r["body_sanitized"])
        if not info:
            continue
        uketuke, edaban, kubun = info
        key = (r["pharmacy_code"] or "", r["dispense_date"] or "", uketuke)
        by_key.setdefault(key, []).append((r["id"], kubun))

    # D を含むキーを抽出 (D と 同じ受付の A/U 両方を削除対象に)
    d_groups = {k: v for k, v in by_key.items() if any(kb == "D" for _, kb in v)}
    print(f"\nD レコードを含む グループ: {len(d_groups)} 件")
    if not d_groups:
        print("削除対象なし")
        return 0

    # 方針C: D と A のみ削除 (U は訂正版=最新として保持)
    target_ids = []
    kept_u = []
    for key, members in d_groups.items():
        pharmacy, disp, uketuke = key
        print(f"\n[{pharmacy} / {disp} / {uketuke}]  (members={len(members)})")
        for pid, kb in members:
            if kb == "U":
                print(f"  id={pid} kubun={kb} → KEEP (訂正版は最新として保持)")
                kept_u.append(pid)
            else:
                print(f"  id={pid} kubun={kb} → 削除")
                target_ids.append(pid)

    print(f"\n合計 削除対象: {len(target_ids)} 件")

    if not args.apply:
        print("\n[dry-run モード] 実際には削除していません。--apply を付けて再実行してください。")
        return 0

    print("\n実削除開始...")
    for pid in target_ids:
        _delete_prescription_cascade(conn, pid)
    conn.commit()
    print(f"完了: {len(target_ids)} 件削除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
