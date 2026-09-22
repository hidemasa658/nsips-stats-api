"""AI チャット用 Tool-use ハンドラ。

Claude Haiku 4.5 に SELECT のみ実行可能な SQL Tool を渡し、
ダッシュボードの内包情報について質問回答できるようにする。
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from datetime import date as _date_type

import anthropic
from dotenv import load_dotenv

load_dotenv()

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOOL_ITER = 8   # 無限ループ防止
_ROW_LIMIT = 200     # 1回の結果行数上限

# 禁止されたキーワード (SQL injection & 書き込み禁止)
_SQL_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH|PRAGMA)\b",
    re.IGNORECASE,
)

# システムプロンプト
_SYSTEM_PROMPT = """あなたはこの薬局のダッシュボード付き AI アシスタントです。
薬局経営者からの質問に、実際の DB データを SQL でクエリして回答します。

## 使用可能テーブル

- `prescriptions` (処方箋): id, source_id, detected_at, dispense_date, dispensed_at,
  clinic_code_enc, clinic_name_enc (原本名保存), prescription_date_enc, doctor_name_enc,
  total_points, drug_fee, dispensing_fee_total, pharmacy_mgmt_fee_total,
  dispensing_base_fee, dispensing_add_fee, drug_guidance_fee, pharmacy_mgmt_other,
  patient_copay, patient_copay_total, senteryoyo_fee_excl_tax, senteryoyo_tax,
  body_sanitized (原本 record 1 除去済)

- `drugs`: id, prescription_id, yj_code, name, quantity, total_quantity,
  unit_price, unit, form (内用/外用/注射), dosage_form_code, rp_no

- `fees`: id, prescription_id, fee_type, code, name (原本の加算名), count, points, is_mix_flag

- `rps`: id, prescription_id, rp_no, usage_code, usage_text, site_text, is_mixed, drug_count

- `drug_pricings` (record 6): id, prescription_id, seq, dispensing_fee, drug_fee_per_unit,
  quantity, total, internal_dispensing_fee

- `mix_events` (事前計算): prescription_id, rp_no, combo (「A + B」), dispense_date, mix_qty, unit

- `fee_master` (加算マスタ): code, name, points, valid_from, valid_to
- `drug_master` (薬品マスタ): yj_code, name, unit, unit_price, generic_name, usage_category

## 重要な注意

1. **dispense_date は YYYYMMDD 文字列** (例: '20260919')
2. 期間フィルタは SUBSTR(dispense_date, 1, 6) = '202609' 等で
3. **fees.name は原本の加算名** (令和6/8年度を跨いで正確)。fee_master.name は現行マスタ (令和8年度) なので、
   歴史的検索は fees.name を使う
4. 混合コンボの集計は mix_events テーブルが速い
5. データ範囲: 2022-02 〜 2026-09 (処方日ベース)

## 回答スタイル

- 数字は 3 桁カンマ区切り
- 患者個人情報 (患者名/住所/生年月日) は絶対に返さない (record 1 は削除済だが念のため)
- 分からない場合は「データ不足」と正直に
- 質問が曖昧な場合は 具体的な期間や薬剤名を確認"""


def _sanitize_sql(sql: str) -> str:
    """SELECT のみ許可。書き込み系や PRAGMA は禁止。行数制限を強制。"""
    sql_stripped = sql.strip().rstrip(";").strip()
    if not sql_stripped.upper().startswith(("SELECT", "WITH")):
        raise ValueError("SELECT または WITH で始まる SQL のみ実行可能です")
    if _SQL_FORBIDDEN.search(sql_stripped):
        raise ValueError("書き込み・DDL・PRAGMA は実行不可")
    # LIMIT がなければ強制付与
    if not re.search(r"\bLIMIT\s+\d+\b", sql_stripped, re.IGNORECASE):
        sql_stripped += f" LIMIT {_ROW_LIMIT}"
    return sql_stripped


def _execute_sql(db_path: Path, sql: str) -> dict:
    """SQL を実行して結果を dict で返す。"""
    try:
        clean = _sanitize_sql(sql)
    except ValueError as e:
        return {"error": str(e)}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA cache_size = -50000")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        rows = conn.execute(clean).fetchmany(_ROW_LIMIT)
        cols = [d[0] for d in conn.execute(clean).description] if rows else []
        result = {
            "columns": cols,
            "rows": [dict(r) for r in rows],
            "row_count": len(rows),
            "sql_executed": clean,
        }
        conn.close()
        return result
    except sqlite3.OperationalError as e:
        return {"error": f"SQL エラー: {e}"}
    except Exception as e:
        return {"error": f"実行エラー: {e}"}


_TOOLS = [
    {
        "name": "execute_sql",
        "description": (
            "SQLite の DB に対して SELECT クエリを実行します。"
            "テーブル定義はシステムプロンプト参照。"
            "書き込み系 SQL は自動で拒否されます。"
            "結果は最大 200 行に制限されます。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "実行する SELECT SQL (SQLite 方言)",
                },
            },
            "required": ["sql"],
        },
    },
]


def ask(question: str, db_path: Path, history: list | None = None) -> dict:
    """質問を受けて Claude Haiku に投げ、Tool-use ループを回して回答を返す。

    戻り値: {"answer": str, "tool_calls": [{"sql": ..., "rows_returned": ...}, ...], "usage": {...}}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY 未設定"}

    # 今日の日付をシステムに追加 (質問の "今月" 等を解釈)
    today = _date_type.today()
    system_full = _SYSTEM_PROMPT + f"""

## 現在時刻
- 今日: {today.isoformat()} (YYYYMMDD 形式: {today.strftime('%Y%m%d')})
- 今月: {today.strftime('%Y%m')} (SUBSTR フィルタ用)
- 先月: {(today.replace(day=1) - __import__('datetime').timedelta(days=1)).strftime('%Y%m')}
- 「今月」「今日」「先月」等はこの日付を基準に解釈してください。ユーザに再確認は不要。
"""

    client = anthropic.Anthropic(api_key=api_key)
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": question})

    tool_calls_log: list[dict] = []
    total_in = 0
    total_out = 0

    for iteration in range(_MAX_TOOL_ITER):
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system_full,
            tools=_TOOLS,
            messages=messages,
        )
        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens

        if response.stop_reason == "end_turn":
            # 最終回答
            text_parts = [b.text for b in response.content if b.type == "text"]
            return {
                "answer": "\n".join(text_parts).strip(),
                "tool_calls": tool_calls_log,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
            }

        if response.stop_reason == "tool_use":
            # Tool 実行して次ターンへ
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "execute_sql":
                    result = _execute_sql(db_path, block.input.get("sql", ""))
                    tool_calls_log.append({
                        "sql": block.input.get("sql", ""),
                        "rows_returned": result.get("row_count", 0),
                        "error": result.get("error"),
                    })
                    # 大きい結果は truncate してから返す
                    import json
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    if len(result_str) > 30000:
                        result_str = result_str[:30000] + "\n... [truncated]"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # その他の stop_reason (max_tokens 等) → 最終
        text_parts = [b.text for b in response.content if b.type == "text"]
        return {
            "answer": "\n".join(text_parts).strip() or "(応答なし)",
            "tool_calls": tool_calls_log,
            "usage": {"input_tokens": total_in, "output_tokens": total_out},
        }

    return {
        "answer": "(Tool 呼び出しが上限に達しました)",
        "tool_calls": tool_calls_log,
        "usage": {"input_tokens": total_in, "output_tokens": total_out},
    }
