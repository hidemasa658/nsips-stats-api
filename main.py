"""nsips-stats-api FastAPI エントリポイント。"""
from __future__ import annotations

import html as html_lib
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from db import init_db

load_dotenv()

API_TOKEN = os.environ.get("API_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "./stats.db")

app = FastAPI(title="nsips-stats-api", version="0.1.0")


def _make_conn() -> sqlite3.Connection:
    """アプリ全体で共有する SQLite 接続を作成。"""
    if DB_PATH == ":memory:":
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if DB_PATH != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    init_db(conn)
    return conn


_conn = _make_conn()


def get_conn() -> sqlite3.Connection:
    return _conn


def verify_token(x_api_token: str | None = Header(default=None)) -> None:
    if not API_TOKEN or x_api_token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/health")
def health(_: None = Depends(verify_token)) -> dict:
    return {"status": "ok"}


from models import IngestPayload, IngestResponse  # noqa: E402


@app.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestPayload, _: None = Depends(verify_token)) -> IngestResponse:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM prescriptions WHERE source_id=?", (payload.source_id,)
    ).fetchone()
    if row is not None:
        return IngestResponse(status="duplicate", prescription_id=row["id"])

    cur = conn.execute(
        """
        INSERT INTO prescriptions
          (source_id, detected_at, body_sanitized, clinic_code_enc, clinic_name_enc,
           prescription_date_enc, doctor_name_enc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.source_id,
            payload.detected_at,
            payload.body_sanitized,
            payload.clinic_code_enc,
            payload.clinic_name_enc,
            payload.prescription_date_enc,
            payload.doctor_name_enc,
        ),
    )
    presc_id = cur.lastrowid

    for d in payload.drugs:
        conn.execute(
            """
            INSERT INTO drugs
              (prescription_id, rp_no_enc, yj_code, name, quantity, unit)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (presc_id, d.rp_no_enc, d.yj_code, d.name, d.quantity, d.unit),
        )

    for f in payload.fees:
        conn.execute(
            """
            INSERT INTO fees
              (prescription_id, fee_type, code_enc, name_enc, code, name, count, points, is_mix_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                presc_id,
                f.fee_type,
                f.code_enc,
                f.name_enc,
                f.code,
                f.name,
                f.count,
                f.points,
                1 if f.is_mix_flag else 0,
            ),
        )

    conn.commit()
    return IngestResponse(status="ok", prescription_id=presc_id)


from models import (  # noqa: E402
    ClinicStat,
    ClinicsResponse,
    DrugStat,
    DrugsResponse,
    MixBreakdown,
    MixResponse,
    PrescriptionOut,
    PrescriptionsExportResponse,
)


@app.get("/stats/drugs", response_model=DrugsResponse)
def stats_drugs(_: None = Depends(verify_token)) -> DrugsResponse:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT yj_code, name, unit, COUNT(*) AS n, SUM(quantity) AS qty
        FROM drugs
        GROUP BY yj_code, name, unit
        ORDER BY n DESC
        """
    ).fetchall()
    return DrugsResponse(
        rows=[
            DrugStat(yj_code=r["yj_code"], name=r["name"], unit=r["unit"], n=r["n"], qty=r["qty"])
            for r in rows
        ]
    )


@app.get("/stats/clinics", response_model=ClinicsResponse)
def stats_clinics(_: None = Depends(verify_token)) -> ClinicsResponse:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT clinic_code_enc, clinic_name_enc, COUNT(*) AS n
        FROM prescriptions
        GROUP BY clinic_code_enc, clinic_name_enc
        ORDER BY n DESC
        """
    ).fetchall()
    return ClinicsResponse(
        rows=[
            ClinicStat(
                clinic_code_enc=r["clinic_code_enc"],
                clinic_name_enc=r["clinic_name_enc"],
                n=r["n"],
            )
            for r in rows
        ]
    )


@app.get("/stats/mix", response_model=MixResponse)
def stats_mix(_: None = Depends(verify_token)) -> MixResponse:
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(DISTINCT prescription_id) FROM fees WHERE is_mix_flag = 1"
    ).fetchone()[0]
    breakdown_rows = conn.execute(
        """
        SELECT drug_count, COUNT(*) AS n FROM (
          SELECT prescription_id, COUNT(*) AS drug_count
          FROM drugs
          WHERE prescription_id IN (
            SELECT DISTINCT prescription_id FROM fees WHERE is_mix_flag = 1
          )
          GROUP BY prescription_id
        )
        GROUP BY drug_count
        ORDER BY drug_count
        """
    ).fetchall()
    return MixResponse(
        total=total,
        breakdown=[MixBreakdown(drug_count=r["drug_count"], n=r["n"]) for r in breakdown_rows],
    )


@app.get("/stats/export/prescriptions", response_model=PrescriptionsExportResponse)
def export_prescriptions(_: None = Depends(verify_token)) -> PrescriptionsExportResponse:
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, source_id, detected_at, clinic_code_enc, clinic_name_enc,
                  prescription_date_enc, doctor_name_enc FROM prescriptions ORDER BY id"""
    ).fetchall()
    return PrescriptionsExportResponse(
        rows=[
            PrescriptionOut(
                id=r["id"],
                source_id=r["source_id"],
                detected_at=r["detected_at"],
                clinic_code_enc=r["clinic_code_enc"],
                clinic_name_enc=r["clinic_name_enc"],
                prescription_date_enc=r["prescription_date_enc"],
                doctor_name_enc=r["doctor_name_enc"],
            )
            for r in rows
        ]
    )


# ==============================================================================
# HTML ダッシュボード (ブラウザ表示用)
# ==============================================================================


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>nsips-stats ダッシュボード</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Meiryo", sans-serif; max-width: 1200px; margin: 20px auto; padding: 0 20px; color: #222; line-height: 1.5; }}
h1 {{ border-bottom: 3px solid #333; padding-bottom: 8px; margin-bottom: 20px; }}
h2 {{ margin-top: 40px; color: #444; border-left: 4px solid #3b82f6; padding-left: 10px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 8px 12px; border-bottom: 1px solid #ddd; text-align: left; }}
th {{ background: #f5f5f5; font-weight: 600; position: sticky; top: 0; }}
tr:hover {{ background: #f9f9f9; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.summary {{ background: #e0f2fe; padding: 16px 24px; border-radius: 8px; margin: 20px 0; display: flex; gap: 40px; flex-wrap: wrap; }}
.summary .item {{ display: flex; flex-direction: column; }}
.summary .big {{ font-size: 28px; font-weight: bold; color: #0369a1; }}
.summary .label {{ font-size: 14px; color: #64748b; }}
.updated {{ color: #888; font-size: 12px; margin-top: 30px; text-align: right; }}
@media (max-width: 640px) {{
  .summary {{ flex-direction: column; gap: 12px; }}
  table {{ font-size: 13px; }}
  th, td {{ padding: 6px 8px; }}
}}
</style>
</head>
<body>
<h1>💊 nsips-stats ダッシュボード</h1>

<div class="summary">
  <div class="item"><span class="big">{prescription_count}</span><span class="label">総処方受入件数</span></div>
  <div class="item"><span class="big">{drug_kinds}</span><span class="label">薬品種類</span></div>
  <div class="item"><span class="big">{mix_total}</span><span class="label">計量混合加算件数</span></div>
</div>

<h2>薬剤別累計 (調剤回数上位 50 品目)</h2>
<table>
<thead><tr><th>YJコード</th><th>薬品名</th><th class="num">回数</th><th class="num">総数量</th><th>単位</th></tr></thead>
<tbody>
{drug_rows}
</tbody>
</table>

<h2>各種加算・料金 累計</h2>
<table>
<thead><tr><th>種別</th><th>加算コード</th><th>加算名</th><th class="num">算定回数</th><th class="num">合計点数</th></tr></thead>
<tbody>
{fee_rows}
</tbody>
</table>

<h2>計量混合加算 内訳</h2>
<table>
<thead><tr><th>混合品目数</th><th class="num">該当件数</th></tr></thead>
<tbody>
{mix_rows}
</tbody>
</table>

<h2>最新受入 生データ (直近 20 件、行をクリックで raw 展開)</h2>
<style>
details {{ margin: 8px 0; padding: 8px; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; }}
details summary {{ cursor: pointer; font-weight: 500; padding: 4px 0; }}
details summary:hover {{ color: #0369a1; }}
details pre {{ background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; margin-top: 8px; font-family: "SFMono-Regular", Menlo, Consolas, monospace; }}
.meta {{ color: #64748b; font-size: 12px; margin-left: 12px; }}
</style>
{recent_rows}

<div class="updated">最終更新: {now} (60秒ごとに自動再読込)</div>
</body>
</html>
"""


def _h(v) -> str:
    return html_lib.escape(str(v) if v is not None else "")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    x_api_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> HTMLResponse:
    provided = x_api_token or token
    if not API_TOKEN or provided != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    conn = get_conn()

    prescription_count = conn.execute("SELECT COUNT(*) FROM prescriptions").fetchone()[0]
    drug_kinds = conn.execute("SELECT COUNT(DISTINCT yj_code) FROM drugs WHERE yj_code IS NOT NULL").fetchone()[0]

    drug_data = conn.execute(
        """SELECT yj_code, name, unit, COUNT(*) AS n, SUM(quantity) AS qty
           FROM drugs GROUP BY yj_code, name, unit ORDER BY n DESC, qty DESC LIMIT 50"""
    ).fetchall()
    drug_rows_html = "\n".join(
        f'<tr><td>{_h(r["yj_code"])}</td><td>{_h(r["name"])}</td>'
        f'<td class="num">{r["n"]}</td><td class="num">{(r["qty"] or 0):.2f}</td>'
        f'<td>{_h(r["unit"])}</td></tr>'
        for r in drug_data
    ) or '<tr><td colspan="5">(データなし)</td></tr>'

    mix_total = conn.execute(
        "SELECT COUNT(DISTINCT prescription_id) FROM fees WHERE is_mix_flag = 1"
    ).fetchone()[0]
    mix_data = conn.execute(
        """SELECT drug_count, COUNT(*) AS n FROM (
             SELECT prescription_id, COUNT(*) AS drug_count FROM drugs
             WHERE prescription_id IN (SELECT DISTINCT prescription_id FROM fees WHERE is_mix_flag = 1)
             GROUP BY prescription_id) GROUP BY drug_count ORDER BY drug_count"""
    ).fetchall()
    mix_rows_html = "\n".join(
        f'<tr><td>{r["drug_count"]} 品目</td><td class="num">{r["n"]}</td></tr>'
        for r in mix_data
    ) or '<tr><td colspan="2">(該当なし)</td></tr>'

    # 加算・料金 累計 (name/code が平文で入っているものだけ集計)
    fee_data = conn.execute(
        """SELECT fee_type, code, name, SUM(count) AS total_count, SUM(points) AS total_points
           FROM fees WHERE name IS NOT NULL
           GROUP BY fee_type, code, name
           ORDER BY total_count DESC, total_points DESC"""
    ).fetchall()
    fee_rows_html = "\n".join(
        f'<tr><td>{_h(r["fee_type"])}</td><td>{_h(r["code"])}</td>'
        f'<td>{_h(r["name"])}</td>'
        f'<td class="num">{r["total_count"] or 0}</td>'
        f'<td class="num">{r["total_points"] or 0}</td></tr>'
        for r in fee_data
    ) or '<tr><td colspan="5">(データなし — 加算情報の暗号化フォーマット変更後の新規受入から表示されます)</td></tr>'

    # 最新受入 生データ 20 件
    recent_data = conn.execute(
        """SELECT p.id, p.detected_at, p.body_sanitized,
                  (SELECT COUNT(*) FROM drugs WHERE prescription_id = p.id) AS drug_n,
                  (SELECT COUNT(*) FROM fees WHERE prescription_id = p.id) AS fee_n
           FROM prescriptions p
           ORDER BY p.id DESC LIMIT 20"""
    ).fetchall()
    recent_rows_html = "\n".join(
        f'<details><summary>#{r["id"]} '
        f'<span class="meta">{_h(r["detected_at"])} · 薬剤 {r["drug_n"]} · 加算 {r["fee_n"]}</span></summary>'
        f'<pre>{_h(r["body_sanitized"] or "(旧クライアントのため生データ未保存)")}</pre>'
        f'</details>'
        for r in recent_data
    ) or '<p>(データなし)</p>'

    html = DASHBOARD_HTML.format(
        prescription_count=prescription_count,
        drug_kinds=drug_kinds,
        drug_rows=drug_rows_html,
        fee_rows=fee_rows_html,
        mix_total=mix_total,
        mix_rows=mix_rows_html,
        recent_rows=recent_rows_html,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return HTMLResponse(content=html)
