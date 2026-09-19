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

    t = payload.totals
    cur = conn.execute(
        """
        INSERT INTO prescriptions
          (source_id, detected_at, dispense_date, dispensed_at,
           body_sanitized, clinic_code_enc, clinic_name_enc,
           prescription_date_enc, doctor_name_enc,
           total_points, dispensing_base_fee, night_holiday_fee,
           management_fee, long_prescription_fee, patient_copay)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.source_id,
            payload.detected_at,
            payload.dispense_date,
            payload.dispensed_at,
            payload.body_sanitized,
            payload.clinic_code_enc,
            payload.clinic_name_enc,
            payload.prescription_date_enc,
            payload.doctor_name_enc,
            t.total_points if t else None,
            t.dispensing_base_fee if t else None,
            t.night_holiday_fee if t else None,
            t.management_fee if t else None,
            t.long_prescription_fee if t else None,
            t.patient_copay if t else None,
        ),
    )
    presc_id = cur.lastrowid

    for d in payload.drugs:
        conn.execute(
            """
            INSERT INTO drugs
              (prescription_id, rp_no_enc, rp_no, yj_code, name, quantity, total_quantity,
               unit_price, unit, form, dosage_form_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                presc_id, d.rp_no_enc, d.rp_no, d.yj_code, d.name,
                d.quantity, d.total_quantity, d.unit_price, d.unit,
                d.form, d.dosage_form_code,
            ),
        )

    for rp in payload.rps:
        conn.execute(
            """
            INSERT INTO rps
              (prescription_id, rp_no, usage_code, usage_text, site_text,
               is_mixed, drug_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                presc_id, rp.rp_no, rp.usage_code, rp.usage_text, rp.site_text,
                1 if rp.is_mixed else 0, rp.drug_count,
            ),
        )

    for dp in payload.drug_pricings:
        conn.execute(
            """
            INSERT INTO drug_pricings
              (prescription_id, seq, dispensing_fee, drug_fee_per_unit, quantity, total,
               internal_dispensing_fee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                presc_id, dp.seq, dp.dispensing_fee, dp.drug_fee_per_unit,
                dp.quantity, dp.total, dp.internal_dispensing_fee,
            ),
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
<h1>💊 nsips-stats ダッシュボード <span style="font-size:14px;color:#64748b;font-weight:normal;">— {period_label}</span></h1>

<style>
.tabs {{ display: flex; gap: 4px; margin: 16px 0; flex-wrap: wrap; }}
.tab {{ padding: 8px 16px; background: #f1f5f9; color: #475569; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 500; transition: all 0.15s; }}
.tab:hover {{ background: #e2e8f0; color: #0f172a; }}
.tab.active {{ background: #3b82f6; color: white; }}
</style>
<div class="tabs">
  <a class="tab {tab_today}" href="?token={token_qs}&period=today">今日</a>
  <a class="tab {tab_yesterday}" href="?token={token_qs}&period=yesterday">昨日</a>
  <a class="tab {tab_month}" href="?token={token_qs}&period=month">今月</a>
  <a class="tab {tab_all}" href="?token={token_qs}&period=all">全期間</a>
</div>

<style>
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 16px 0; }}
.kpi {{ background: #f8fafc; border-left: 3px solid #3b82f6; padding: 10px 14px; border-radius: 6px; }}
.kpi.accent-orange {{ border-left-color: #f59e0b; background: #fffbeb; }}
.kpi.accent-green {{ border-left-color: #10b981; background: #ecfdf5; }}
.kpi.accent-purple {{ border-left-color: #8b5cf6; background: #f5f3ff; }}
.kpi .val {{ font-size: 22px; font-weight: bold; color: #0f172a; font-variant-numeric: tabular-nums; }}
.kpi .lbl {{ font-size: 11px; color: #475569; margin-top: 2px; }}
.kpi-section-title {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin: 20px 0 4px; padding-left: 4px; }}
</style>

<div class="kpi-section-title">{main_label} / {cmp_label} (調剤日ベース)</div>
<div class="kpi-grid">
  <div class="kpi accent-green"><div class="val">{main_count}</div><div class="lbl">{main_label} 処方件数</div></div>
  <div class="kpi accent-green"><div class="val">{main_points:,}</div><div class="lbl">{main_label} 請求点数</div></div>
  <div class="kpi accent-green"><div class="val">{main_copay:,}</div><div class="lbl">{main_label} 患者負担金</div></div>
  <div class="kpi"><div class="val">{cmp_count}</div><div class="lbl">{cmp_label} 処方件数</div></div>
  <div class="kpi"><div class="val">{cmp_points:,}</div><div class="lbl">{cmp_label} 請求点数</div></div>
  <div class="kpi"><div class="val">{cmp_copay:,}</div><div class="lbl">{cmp_label} 患者負担金</div></div>
</div>

<div class="kpi-section-title">基本 (全期間)</div>
<div class="kpi-grid">
  <div class="kpi accent-purple"><div class="val">{prescription_count}</div><div class="lbl">総処方受入件数</div></div>
  <div class="kpi accent-purple"><div class="val">{drug_kinds}</div><div class="lbl">薬品種類</div></div>
  <div class="kpi accent-orange"><div class="val">{form_internal}</div><div class="lbl">内用 (回数)</div></div>
  <div class="kpi accent-orange"><div class="val">{form_external}</div><div class="lbl">外用 (回数)</div></div>
  <div class="kpi accent-orange"><div class="val">{form_injection}</div><div class="lbl">注射 (回数)</div></div>
  <div class="kpi accent-orange"><div class="val">{form_other}</div><div class="lbl">その他 (回数)</div></div>
  <div class="kpi accent-orange"><div class="val">{mix_total}</div><div class="lbl">計量混合加算 件数</div></div>
</div>

<div class="kpi-section-title">経営集計 (record 5)</div>
<div class="kpi-grid">
  <div class="kpi accent-green"><div class="val">{t_total_points:,}</div><div class="lbl">総請求点数</div></div>
  <div class="kpi accent-green"><div class="val">{t_patient_copay:,}</div><div class="lbl">総患者負担金 (円)</div></div>
  <div class="kpi"><div class="val">{t_dispensing_base:,}</div><div class="lbl">調剤基本料</div></div>
  <div class="kpi"><div class="val">{t_night_holiday:,}</div><div class="lbl">夜間・休日等加算</div></div>
  <div class="kpi"><div class="val">{t_management:,}</div><div class="lbl">薬学管理料</div></div>
  <div class="kpi"><div class="val">{t_long_prescription:,}</div><div class="lbl">長期処方関連</div></div>
</div>

<div class="kpi-section-title">基本料 累計 (record 6 基本料バリアント)</div>
<div class="kpi-grid">
  <div class="kpi"><div class="val">{dp_total_dispensing:,}</div><div class="lbl">調剤料 合計 (点)</div></div>
  <div class="kpi"><div class="val">{dp_total_drug_fee:,}</div><div class="lbl">薬剤料 合計 (点)</div></div>
  <div class="kpi"><div class="val">{dp_count:,}</div><div class="lbl">剤 (record 6 行数)</div></div>
  <div class="kpi"><div class="val">{dp_internal_total:,}</div><div class="lbl">内服調剤料 累計 (点)</div></div>
  <div class="kpi"><div class="val">{dp_long_count:,}</div><div class="lbl">長期処方 28日以上 (60点/剤)</div></div>
  <div class="kpi"><div class="val">{dp_short_count:,}</div><div class="lbl">短期処方 27日以下 (10点/剤)</div></div>
</div>

<h2>期間別 集計</h2>
<div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px;">
  <div>
    <h3 style="font-size:14px;color:#475569;margin:8px 0;">日別 (直近 30 日)</h3>
    <table style="font-size:12px;">
      <thead><tr><th>日付</th><th class="num">件数</th><th class="num">点数</th><th class="num">負担金</th></tr></thead>
      <tbody>
      {daily_rows}
      </tbody>
    </table>
  </div>
  <div>
    <h3 style="font-size:14px;color:#475569;margin:8px 0;">月別</h3>
    <table style="font-size:12px;">
      <thead><tr><th>月</th><th class="num">件数</th><th class="num">点数</th><th class="num">負担金</th></tr></thead>
      <tbody>
      {monthly_rows}
      </tbody>
    </table>
  </div>
</div>

<h2>成分別 累計 (YJ 1〜7 桁: 同一成分でまとめる)</h2>
<p style="color:#64748b;font-size:12px;">同じ成分の 先発品・後発品・別剤形をまとめて集計。品目数 &gt; 1 は同じ成分の複数バリエーションが処方された = 後発切替検討や剤形選択の余地あり。</p>
<table>
<thead><tr><th>YJ (1-7)</th><th>成分 (代表薬品名 / 一般名)</th><th class="num">品目数</th><th class="num">調剤回数</th><th class="num">総数量</th></tr></thead>
<tbody>
{ingredient_rows}
</tbody>
</table>

<h2>薬剤別累計 (調剤回数上位 50 品目)</h2>
<style>
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
.badge-in {{ background: #fef3c7; color: #92400e; }}
.badge-ext {{ background: #dbeafe; color: #1e40af; }}
.badge-inj {{ background: #fce7f3; color: #9f1239; }}
.badge-other {{ background: #e5e7eb; color: #4b5563; }}
.generic {{ color: #64748b; font-size: 11px; }}
</style>
<table>
<thead><tr><th>剤形</th><th>YJコード</th><th>薬品名 / 一般名</th><th class="num">回数</th><th class="num">総数量</th><th>単位</th><th class="num">薬価</th></tr></thead>
<tbody>
{drug_rows}
</tbody>
</table>

<h2>混合処方 (外用剤の計量混合) 集計</h2>
<p style="color:#64748b;font-size:13px;">record 3 field 5 が「混合」の RP + 外用剤 (M/N/Q/X/U/P) の組合せのみ集計。MIX 量 = 同 RP 内の外用剤 総処方量の合計。「30g × 5件」= 合計 30g の混合が 5 回。</p>
<table>
<thead><tr><th class="num">総件数</th><th>混合された薬剤の組合せ</th><th>MIX 量別 内訳 (量 × 件数)</th></tr></thead>
<tbody>
{mix_combos}
</tbody>
</table>

<h2>各種加算・料金 累計</h2>
<table>
<thead><tr><th>種別</th><th>加算コード</th><th>加算名</th><th class="num">算定回数</th><th class="num">合計点数</th></tr></thead>
<tbody>
{fee_rows}
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
    period: str | None = Query(default="all"),  # all / today / yesterday / month / YYYYMM
) -> HTMLResponse:
    provided = x_api_token or token
    if not API_TOKEN or provided != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    conn = get_conn()

    # ---- 期間フィルタ ----
    from datetime import datetime, timedelta
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
    this_month = now.strftime("%Y%m")

    if period == "today":
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{today_str}'"
        period_label = f"今日 ({now:%Y-%m-%d})"
    elif period == "yesterday":
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{yesterday_str}'"
        period_label = f"昨日 ({(now - timedelta(days=1)):%Y-%m-%d})"
    elif period == "month":
        period_where = f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{this_month}'"
        period_label = f"今月 ({now:%Y-%m})"
    elif period and len(period) == 6 and period.isdigit():
        period_where = f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{period}'"
        period_label = f"{period[:4]}-{period[4:6]}"
    else:
        period_where = "1=1"
        period_label = "全期間"
        period = "all"

    prescription_count = conn.execute(f"SELECT COUNT(*) FROM prescriptions WHERE {period_where}").fetchone()[0]
    drug_kinds = conn.execute(
        f"SELECT COUNT(DISTINCT d.yj_code) FROM drugs d JOIN prescriptions p ON d.prescription_id = p.id WHERE d.yj_code IS NOT NULL AND {period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')}"
    ).fetchone()[0]

    drug_data = conn.execute(
        f"""SELECT d.yj_code,
                  COALESCE(dm.name, d.name) AS drug_name,
                  COALESCE(dm.unit, d.unit) AS drug_unit,
                  dm.usage_category,
                  d.form AS client_form,
                  dm.unit_price AS master_price,
                  dm.generic_name,
                  COUNT(*) AS n,
                  SUM(d.total_quantity) AS qty,
                  SUM(CASE WHEN d.total_quantity IS NOT NULL THEN 1 ELSE 0 END) AS valid_n
           FROM drugs d
           JOIN prescriptions p ON d.prescription_id = p.id
           LEFT JOIN drug_master dm ON d.yj_code = dm.yj_code
           WHERE {period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')}
           GROUP BY d.yj_code, drug_name, drug_unit, dm.usage_category, d.form, dm.unit_price, dm.generic_name
           ORDER BY n DESC, qty DESC LIMIT 50"""
    ).fetchall()

    # 薬品名からの 剤形 キーワード判定 (順序が重要 - 長いキーワード優先)
    _FORM_KEYWORDS = [
        ("錠剤", ["錠"]),
        ("カプセル", ["カプセル"]),
        ("散剤", ["散", "末"]),
        ("顆粒", ["顆粒", "細粒"]),
        ("シロップ", ["シロップ", "ドライシロップ"]),
        ("内用液", ["内服液", "経口液"]),
        ("軟膏", ["軟膏"]),
        ("クリーム", ["クリーム"]),
        ("ローション", ["ローション"]),
        ("ゲル", ["ゲル"]),
        ("スプレー", ["スプレー", "エアゾール", "エアロゾル"]),
        ("貼付剤", ["貼付", "テープ", "パッチ", "パップ", "湿布"]),
        ("坐剤", ["坐剤", "坐薬", "座薬"]),
        ("点眼液", ["点眼"]),
        ("点鼻剤", ["点鼻"]),
        ("点耳液", ["点耳"]),
        ("含嗽剤", ["うがい", "含嗽"]),
        ("うがい薬", ["うがい"]),
        ("皮膚基剤", ["ワセリン", "プロペト"]),
        ("注射剤", ["注射", "アンプル", "バイアル", "注"]),
        ("キット", ["キット"]),
    ]

    def _detect_form_from_name(name: str | None) -> str | None:
        if not name:
            return None
        for form_name, keywords in _FORM_KEYWORDS:
            for kw in keywords:
                if kw in name:
                    return form_name
        return None

    def _detailed_form(yj_code: str | None, drug_name: str | None, client_form: str | None) -> tuple[str, str]:
        """(category, 詳細剤形) を返す。category は badge 色に使う。

        判別優先順位:
        1. 薬品名からのキーワード判定 (最も信頼できる)
        2. YJ 5-7桁 投与経路 + 8桁目 letter mapping (フォールバック)
        """
        # 1. 名前から剤形
        name_form = _detect_form_from_name(drug_name)

        # カテゴリ判定 (badge 色用): YJ 5-7桁 → client form
        usage_cat = None
        if yj_code and len(yj_code) >= 7:
            route_code = yj_code[4:7]
            if route_code.isdigit():
                n = int(route_code)
                if 1 <= n <= 399:
                    usage_cat = "1"
                elif 400 <= n <= 699:
                    usage_cat = "4"
                elif 700 <= n <= 999:
                    usage_cat = "6"
        if usage_cat is None:
            if client_form == "内用" or client_form == "内服":
                usage_cat = "1"
            elif client_form == "注射":
                usage_cat = "4"
            elif client_form == "外用":
                usage_cat = "6"

        cat = {"1": "in", "4": "inj", "6": "ext"}.get(usage_cat or "", "other")

        if name_form:
            return (cat, name_form)

        # フォールバック: 8桁目 letter mapping
        if not yj_code or len(yj_code) < 8 or usage_cat is None:
            return (cat, client_form or "?")
        letter = yj_code[7]
        if usage_cat == "1":
            if letter in "ABCDE": return (cat, "散剤")
            if letter in "FGHIJKL": return (cat, "錠剤")
            if letter in "MNOP": return (cat, "液剤")
            return (cat, "内用その他")
        elif usage_cat == "4":
            return (cat, "注射剤")
        elif usage_cat == "6":
            return (cat, "外用その他")
        return (cat, "?")

    def _form_badge_detailed(yj_code, drug_name, client_form):
        cat, label = _detailed_form(yj_code, drug_name, client_form)
        cls = f"badge-{cat}"
        return f'<span class="badge {cls}">{label}</span>'

    def _drug_row(r):
        generic = f'<div class="generic">{_h(r["generic_name"])}</div>' if r["generic_name"] else ""
        price = f'{r["master_price"]:.2f} 円' if r["master_price"] else ""
        # 総数量の信頼度: valid_n = 新パーサーで total_quantity が計算できた件数
        qty_val = r["qty"] or 0
        if r["valid_n"] and r["valid_n"] < r["n"]:
            # 一部のみ新データ
            qty_display = f'{qty_val:.2f} <span class="generic">(内 {r["valid_n"]}/{r["n"]} 件)</span>'
        elif r["valid_n"]:
            qty_display = f'{qty_val:.2f}'
        else:
            qty_display = '<span class="generic">旧データ (要 .txt 再来)</span>'
        return (
            f'<tr><td>{_form_badge_detailed(r["yj_code"], r["drug_name"], r["client_form"])}</td>'
            f'<td>{_h(r["yj_code"])}</td>'
            f'<td>{_h(r["drug_name"])}{generic}</td>'
            f'<td class="num">{r["n"]}</td>'
            f'<td class="num">{qty_display}</td>'
            f'<td>{_h(r["drug_unit"])}</td>'
            f'<td class="num">{price}</td></tr>'
        )
    drug_rows_html = "\n".join(_drug_row(r) for r in drug_data) or '<tr><td colspan="7">(データなし)</td></tr>'

    # 成分別 累計 (YJ 1-7 桁)
    ingredient_data = conn.execute(
        """SELECT SUBSTR(d.yj_code, 1, 7) AS ingredient_code,
                  MAX(COALESCE(dm.generic_name, dm.name, d.name)) AS repr_name,
                  COUNT(DISTINCT d.yj_code) AS variant_count,
                  COUNT(*) AS n,
                  SUM(d.total_quantity) AS qty
           FROM drugs d
           LEFT JOIN drug_master dm ON d.yj_code = dm.yj_code
           WHERE d.yj_code IS NOT NULL AND LENGTH(d.yj_code) >= 7
           GROUP BY ingredient_code
           ORDER BY n DESC, qty DESC LIMIT 30"""
    ).fetchall()
    ingredient_rows_html = "\n".join(
        f'<tr><td>{_h(r["ingredient_code"])}</td>'
        f'<td>{_h(r["repr_name"])}'
        + (f' <span class="generic">({r["variant_count"]} 品目)</span>' if r["variant_count"] > 1 else "")
        + f'</td>'
        f'<td class="num">{r["variant_count"]}</td>'
        f'<td class="num">{r["n"]}</td>'
        f'<td class="num">{(r["qty"] or 0):.2f}</td></tr>'
        for r in ingredient_data
    ) or '<tr><td colspan="5">(データなし)</td></tr>'

    # 選択タブに応じた 主 KPI と 比較 KPI (dispense_date ベース)
    def _kpi_by_where(where_clause: str) -> dict:
        row = conn.execute(
            f"""SELECT COUNT(*) n,
                       COALESCE(SUM(total_points), 0) pts,
                       COALESCE(SUM(patient_copay), 0) cp
                FROM prescriptions
                WHERE {where_clause}"""
        ).fetchone()
        return {"n": row["n"], "pts": row["pts"], "cp": row["cp"]}

    def _by_date(d: str) -> str:
        return f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{d}'"

    def _by_month(m: str) -> str:
        return f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{m}'"

    today_str_ = now.strftime("%Y%m%d")
    yday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
    dbyday = (now - timedelta(days=2)).strftime("%Y%m%d")
    this_month_ = now.strftime("%Y%m")
    last_month_dt = (now.replace(day=1) - timedelta(days=1))
    last_month = last_month_dt.strftime("%Y%m")

    if period == "yesterday":
        main_agg = _kpi_by_where(_by_date(yday_str))
        cmp_agg = _kpi_by_where(_by_date(dbyday))
        main_label = f"昨日 ({(now - timedelta(days=1)):%m-%d})"
        cmp_label = f"一昨日 ({(now - timedelta(days=2)):%m-%d})"
    elif period == "month":
        main_agg = _kpi_by_where(_by_month(this_month_))
        cmp_agg = _kpi_by_where(_by_month(last_month))
        main_label = f"今月 ({now:%Y-%m})"
        cmp_label = f"先月 ({last_month_dt:%Y-%m})"
    elif period == "all":
        main_agg = _kpi_by_where("1=1")
        cmp_agg = _kpi_by_where(_by_month(this_month_))
        main_label = "全期間 (累計)"
        cmp_label = f"今月 ({now:%Y-%m})"
    else:  # today (default)
        main_agg = _kpi_by_where(_by_date(today_str_))
        cmp_agg = _kpi_by_where(_by_date(yday_str))
        main_label = f"今日 ({now:%m-%d})"
        cmp_label = f"昨日 ({(now - timedelta(days=1)):%m-%d})"

    # 日別集計 (直近 30 日、dispense_date 優先)
    daily_data = conn.execute(
        """SELECT COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) AS d,
                  COUNT(*) AS n,
                  COALESCE(SUM(total_points), 0) AS pts,
                  COALESCE(SUM(patient_copay), 0) AS cp
           FROM prescriptions
           GROUP BY d
           ORDER BY d DESC LIMIT 30"""
    ).fetchall()
    daily_rows_html = "\n".join(
        f'<tr><td>{_h(r["d"])}</td>'
        f'<td class="num">{r["n"]}</td>'
        f'<td class="num">{r["pts"]:,}</td>'
        f'<td class="num">{r["cp"]:,}</td></tr>'
        for r in daily_data
    ) or '<tr><td colspan="4">(データなし)</td></tr>'

    # 月別集計 (全期間、dispense_date 優先)
    monthly_data = conn.execute(
        """SELECT COALESCE(SUBSTR(dispense_date, 1, 6), SUBSTR(REPLACE(detected_at,'-',''), 1, 6)) AS m,
                  COUNT(*) AS n,
                  COALESCE(SUM(total_points), 0) AS pts,
                  COALESCE(SUM(patient_copay), 0) AS cp
           FROM prescriptions
           GROUP BY m
           ORDER BY m DESC"""
    ).fetchall()
    monthly_rows_html = "\n".join(
        f'<tr><td>{_h(r["m"])}</td>'
        f'<td class="num">{r["n"]}</td>'
        f'<td class="num">{r["pts"]:,}</td>'
        f'<td class="num">{r["cp"]:,}</td></tr>'
        for r in monthly_data
    ) or '<tr><td colspan="4">(データなし)</td></tr>'

    # record 5 全体集計の累計 (経営指標、期間フィルタ適用)
    t_agg = conn.execute(
        f"""SELECT
             COALESCE(SUM(total_points), 0) AS total_points,
             COALESCE(SUM(patient_copay), 0) AS patient_copay,
             COALESCE(SUM(dispensing_base_fee), 0) AS dispensing_base,
             COALESCE(SUM(night_holiday_fee), 0) AS night_holiday,
             COALESCE(SUM(management_fee), 0) AS management,
             COALESCE(SUM(long_prescription_fee), 0) AS long_prescription
           FROM prescriptions
           WHERE {period_where}"""
    ).fetchone()

    # 基本料 (record 6 基本料バリアント) の累計、期間フィルタ
    dp_where = period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')
    dp_agg = conn.execute(
        f"""SELECT
             COALESCE(SUM(dp.dispensing_fee), 0) AS total_dispensing,
             COALESCE(SUM(dp.drug_fee_per_unit * dp.quantity), 0) AS total_drug_fee,
             COALESCE(SUM(dp.internal_dispensing_fee), 0) AS internal_dispensing_total,
             COALESCE(SUM(CASE WHEN dp.internal_dispensing_fee = 60 THEN 1 ELSE 0 END), 0) AS long_count,
             COALESCE(SUM(CASE WHEN dp.internal_dispensing_fee = 10 THEN 1 ELSE 0 END), 0) AS short_count,
             COUNT(*) AS n
           FROM drug_pricings dp
           JOIN prescriptions p ON dp.prescription_id = p.id
           WHERE {dp_where}"""
    ).fetchone()
    dp_total_dispensing = dp_agg["total_dispensing"]
    dp_total_drug_fee = dp_agg["total_drug_fee"]
    dp_internal_total = dp_agg["internal_dispensing_total"]
    dp_long_count = dp_agg["long_count"]
    dp_short_count = dp_agg["short_count"]
    dp_count = dp_agg["n"]

    # 剤形別 サマリー (YJ 5-7桁 で判別、期間フィルタ)
    form_summary = dict(
        conn.execute(
            f"""SELECT
                 CASE
                   WHEN LENGTH(d.yj_code) >= 7 AND SUBSTR(d.yj_code, 5, 3) GLOB '[0-9][0-9][0-9]' AND CAST(SUBSTR(d.yj_code, 5, 3) AS INTEGER) BETWEEN 1 AND 399 THEN '内用'
                   WHEN LENGTH(d.yj_code) >= 7 AND SUBSTR(d.yj_code, 5, 3) GLOB '[0-9][0-9][0-9]' AND CAST(SUBSTR(d.yj_code, 5, 3) AS INTEGER) BETWEEN 400 AND 699 THEN '注射'
                   WHEN LENGTH(d.yj_code) >= 7 AND SUBSTR(d.yj_code, 5, 3) GLOB '[0-9][0-9][0-9]' AND CAST(SUBSTR(d.yj_code, 5, 3) AS INTEGER) BETWEEN 700 AND 999 THEN '外用'
                   ELSE COALESCE(d.form, 'その他')
                 END AS cat,
                 COUNT(*)
               FROM drugs d
               JOIN prescriptions p ON d.prescription_id = p.id
               WHERE {period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')}
               GROUP BY cat"""
        ).fetchall()
    )
    form_internal = form_summary.get("内用", 0) + form_summary.get("内服", 0)
    form_external = form_summary.get("外用", 0)
    form_injection = form_summary.get("注射", 0)
    form_other = form_summary.get("その他", 0)

    # 混合処方: combo × 量 で集計 → Python で combo ごとに内訳を組立
    combos_data = conn.execute(
        """
        WITH mix AS (
          SELECT GROUP_CONCAT(d.name, ' + ') AS combo,
                 ROUND(SUM(COALESCE(d.quantity, 0)), 2) AS mix_qty,
                 MAX(d.unit) AS unit
          FROM rps r
          JOIN drugs d ON d.prescription_id = r.prescription_id AND d.rp_no = r.rp_no
          WHERE r.is_mixed = 1
            AND r.site_text = '混合'          -- 誤検出 (旧 drug_count>=2 判定) を除外
            AND d.name IS NOT NULL
            AND d.form = '外用'
          GROUP BY r.id
        )
        SELECT combo, mix_qty, unit, COUNT(*) AS n
        FROM mix
        WHERE combo IS NOT NULL
        GROUP BY combo, mix_qty, unit
        ORDER BY combo, mix_qty
        """
    ).fetchall()

    # Python 側で combo ごとにグルーピング + 総件数計算
    from collections import defaultdict
    combo_map: dict = defaultdict(list)
    combo_total: dict = defaultdict(int)
    for r in combos_data:
        combo_map[r["combo"]].append((r["mix_qty"], r["unit"], r["n"]))
        combo_total[r["combo"]] += r["n"]

    sorted_combos = sorted(combo_map.keys(), key=lambda c: -combo_total[c])[:30]

    def _fmt_breakdown(entries):
        # 量が多い順にソート
        entries.sort(key=lambda x: -(x[0] or 0))
        parts = []
        for qty, unit, n in entries:
            qty_str = f"{qty:g}" if qty else "0"
            parts.append(f'<span style="display:inline-block;background:#e0f2fe;border-radius:12px;padding:2px 10px;margin:2px 4px 2px 0;font-size:12px;">{qty_str}{_h(unit or "")}×{n}</span>')
        return "".join(parts)

    mix_combos_html = "\n".join(
        f'<tr><td class="num"><strong>{combo_total[c]}</strong></td>'
        f'<td>{_h(c)}</td>'
        f'<td>{_fmt_breakdown(combo_map[c])}</td></tr>'
        for c in sorted_combos
    ) or '<tr><td colspan="3">(データなし — 新クライアントから RP 情報 (is_mixed) が届き次第表示)</td></tr>'

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

    # 加算・料金 累計 (fee_master と JOIN、マスタ名/点数優先)
    fee_data = conn.execute(
        """SELECT
             f.fee_type,
             f.code,
             COALESCE(m.name, f.name) AS display_name,
             m.points AS master_points,
             SUM(COALESCE(f.count, 1)) AS total_count,
             SUM(COALESCE(f.count, 1) * COALESCE(m.points, f.points, 0)) AS total_points
           FROM fees f
           LEFT JOIN fee_master m ON f.code = m.code
           WHERE f.code IS NOT NULL
             AND (LENGTH(f.code) >= 5 OR m.code IS NOT NULL)  -- 実加算コードは通常 9 桁、防御的フィルタ
           GROUP BY f.fee_type, f.code, display_name, master_points
           ORDER BY total_count DESC, total_points DESC"""
    ).fetchall()
    def _fee_row(r):
        master_pts = r["master_points"]
        pts_hint = f' <span style="color:#64748b;font-size:11px;">({master_pts}点/回)</span>' if master_pts is not None else ""
        return (
            f'<tr><td>{_h(r["fee_type"])}</td><td>{_h(r["code"])}</td>'
            f'<td>{_h(r["display_name"])}{pts_hint}</td>'
            f'<td class="num">{r["total_count"] or 0}</td>'
            f'<td class="num">{r["total_points"] or 0}</td></tr>'
        )
    fee_rows_html = "\n".join(_fee_row(r) for r in fee_data) or '<tr><td colspan="5">(データなし)</td></tr>'

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
        mix_combos=mix_combos_html,
        form_internal=form_internal,
        form_external=form_external,
        form_injection=form_injection,
        form_other=form_other,
        daily_rows=daily_rows_html,
        monthly_rows=monthly_rows_html,
        ingredient_rows=ingredient_rows_html,
        main_count=main_agg["n"],
        main_points=main_agg["pts"],
        main_copay=main_agg["cp"],
        cmp_count=cmp_agg["n"],
        cmp_points=cmp_agg["pts"],
        cmp_copay=cmp_agg["cp"],
        main_label=main_label,
        cmp_label=cmp_label,
        period_label=period_label,
        token_qs=provided,
        tab_today="active" if period == "today" else "",
        tab_yesterday="active" if period == "yesterday" else "",
        tab_month="active" if period == "month" else "",
        tab_all="active" if period == "all" else "",
        dp_total_dispensing=dp_total_dispensing,
        dp_total_drug_fee=dp_total_drug_fee,
        dp_count=dp_count,
        dp_internal_total=dp_internal_total,
        dp_long_count=dp_long_count,
        dp_short_count=dp_short_count,
        t_total_points=t_agg["total_points"],
        t_patient_copay=t_agg["patient_copay"],
        t_dispensing_base=t_agg["dispensing_base"],
        t_night_holiday=t_agg["night_holiday"],
        t_management=t_agg["management"],
        t_long_prescription=t_agg["long_prescription"],
        recent_rows=recent_rows_html,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return HTMLResponse(content=html)
