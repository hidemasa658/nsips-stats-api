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
           total_points, drug_fee, dispensing_fee_total, pharmacy_mgmt_fee_total,
           dispensing_base_fee, dispensing_add_fee, drug_guidance_fee,
           pharmacy_mgmt_other, patient_copay, patient_copay_total,
           senteryoyo_fee_excl_tax, senteryoyo_tax)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            t.drug_fee if t else None,
            t.dispensing_fee_total if t else None,
            t.pharmacy_mgmt_fee_total if t else None,
            t.dispensing_base_fee if t else None,
            t.dispensing_add_fee if t else None,
            t.drug_guidance_fee if t else None,
            t.pharmacy_mgmt_other if t else None,
            t.patient_copay if t else None,
            t.patient_copay_total if t else None,
            t.senteryoyo_fee_excl_tax if t else None,
            t.senteryoyo_tax if t else None,
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
    # 新規データが入ったら dashboard キャッシュを破棄
    global _DASHBOARD_CACHE
    try:
        _DASHBOARD_CACHE.clear()
    except (NameError, AttributeError):
        pass
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
<meta http-equiv="refresh" content="600">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>nsips-stats ダッシュボード</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
html, body {{ background: #ffffff; }}
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
.tabs {{ display: flex; gap: 4px; margin: 12px 0; flex-wrap: wrap; align-items: center; }}
.tab {{ padding: 6px 12px; background: #f1f5f9; color: #475569; border-radius: 5px; text-decoration: none; font-size: 12px; font-weight: 500; transition: all 0.15s; }}
.tab:hover {{ background: #e2e8f0; color: #0f172a; }}
.tab.active {{ background: #3b82f6; color: white; }}
.tabs .sep {{ width: 1px; height: 20px; background: #cbd5e1; margin: 0 4px; }}
.tabs input[type="date"], .tabs input[type="month"] {{
  padding: 5px 8px; font-size: 12px; border: 1px solid #cbd5e1; border-radius: 5px;
  background: #fff; color: #0f172a; font-family: inherit;
}}
.tabs form {{ display: inline-flex; gap: 4px; align-items: center; margin: 0; }}
.tabs button {{ padding: 5px 10px; background: #f1f5f9; color: #475569; border: none; border-radius: 5px; font-size: 12px; cursor: pointer; }}
.tabs button:hover {{ background: #e2e8f0; color: #0f172a; }}
</style>
<div class="tabs">
  <a class="tab {tab_today}" href="?token={token_qs}&period=today">今日</a>
  <a class="tab {tab_yesterday}" href="?token={token_qs}&period=yesterday">昨日</a>
  <a class="tab {tab_month}" href="?token={token_qs}&period=month">今月</a>
  <a class="tab {tab_all}" href="?token={token_qs}&period=all">全期間</a>
  <span class="sep"></span>
  <form method="get">
    <input type="hidden" name="token" value="{token_qs}">
    <input type="date" name="period" value="{picker_date}" onchange="this.form.submit()">
  </form>
  <form method="get">
    <input type="hidden" name="token" value="{token_qs}">
    <input type="month" name="period" value="{picker_month}" onchange="this.form.submit()">
  </form>
</div>

<style>
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 6px; margin: 10px 0; }}
.kpi {{ background: #f8fafc; border-left: 3px solid #3b82f6; padding: 6px 10px; border-radius: 4px; }}
.kpi.accent-orange {{ border-left-color: #f59e0b; background: #fffbeb; }}
.kpi.accent-green {{ border-left-color: #10b981; background: #ecfdf5; }}
.kpi.accent-purple {{ border-left-color: #8b5cf6; background: #f5f3ff; }}
.kpi.accent-blue {{ border-left-color: #3b82f6; background: #eff6ff; }}
.kpi .val {{ font-size: 17px; font-weight: bold; color: #0f172a; font-variant-numeric: tabular-nums; line-height: 1.2; }}
.kpi .lbl {{ font-size: 10px; color: #64748b; margin-top: 1px; }}
.kpi-section-title {{ font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin: 14px 0 2px; padding-left: 4px; }}

/* 調剤報酬明細書スタイル */
.receipt {{ max-width: 640px; margin: 12px 0; font-family: "Hiragino Sans", "Yu Gothic", sans-serif; }}
.receipt table {{ width: 100%; border-collapse: collapse; background: #fff; border: 2px solid #334155; }}
.receipt th, .receipt td {{ padding: 8px 12px; border-bottom: 1px solid #cbd5e1; vertical-align: middle; }}
.receipt .cat-header {{ background: #f1f5f9; font-weight: bold; color: #0f172a; border-top: 1.5px solid #334155; }}
.receipt .cat-header td {{ padding: 6px 12px; font-size: 13px; }}
.receipt .item-name {{ color: #334155; font-size: 13px; padding-left: 24px; }}
.receipt .item-code {{ color: #94a3b8; font-size: 10px; margin-left: 4px; }}
.receipt .num {{ text-align: right; font-variant-numeric: tabular-nums; font-size: 14px; color: #0f172a; min-width: 90px; }}
.receipt .subtotal {{ background: #fafafa; font-weight: 600; }}
.receipt .subtotal td {{ padding: 6px 12px; color: #475569; }}
.receipt .subtotal .num {{ font-size: 15px; color: #0f172a; }}
.receipt .grand {{ background: #0f172a; color: #fff; font-weight: bold; border-top: 2px solid #0f172a; }}
.receipt .grand td {{ padding: 10px 12px; color: #fff; }}
.receipt .grand .num {{ font-size: 18px; color: #fff; }}
.receipt .copay {{ background: #fef3c7; font-weight: bold; }}
.receipt .copay td {{ padding: 8px 12px; color: #78350f; }}
.receipt .copay .num {{ font-size: 16px; color: #78350f; }}

/* 混合処方 週次トレンド */
.mix-summary {{ display: flex; gap: 12px; margin: 12px 0; flex-wrap: wrap; }}
.mix-kpi {{ background: #fef3c7; border-left: 4px solid #d97706; padding: 8px 14px; border-radius: 6px; }}
.mix-kpi .v {{ font-size: 22px; font-weight: bold; color: #78350f; font-variant-numeric: tabular-nums; }}
.mix-kpi .l {{ font-size: 11px; color: #92400e; }}
.chart-wrap {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px; overflow-x: auto; }}
.chart-wrap svg {{ display: block; }}
.chart-wrap svg .bar {{ fill: #f59e0b; }}
.chart-wrap svg .bar:hover {{ fill: #d97706; }}
.chart-wrap svg .axis {{ stroke: #94a3b8; stroke-width: 1; }}
.chart-wrap svg .lbl {{ font-size: 9px; fill: #64748b; text-anchor: middle; }}
.chart-wrap svg .val {{ font-size: 10px; fill: #0f172a; text-anchor: middle; font-weight: 600; }}
.chart-wrap svg .grid {{ stroke: #e5e7eb; stroke-width: 1; stroke-dasharray: 3,3; }}

/* 月別ヒートマップ */
.heatmap-wrap {{ overflow-x: auto; margin: 12px 0; }}
.heatmap-wrap table {{ border-collapse: collapse; font-size: 11px; }}
.heatmap-wrap th, .heatmap-wrap td {{ padding: 3px 5px; border: 1px solid #f1f5f9; text-align: center; }}
.heatmap-wrap .hm-label {{ text-align: left; padding: 3px 8px; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: #f8fafc; font-size: 11px; position: sticky; left: 0; z-index: 1; }}
.heatmap-wrap .hm-month {{ background: #f1f5f9; color: #475569; font-weight: 500; font-size: 10px; padding: 4px 3px; }}
.heatmap-wrap .hm-cell {{ font-variant-numeric: tabular-nums; color: #0f172a; min-width: 26px; }}
.heatmap-wrap .hm-0 {{ background: #fff; color: #cbd5e1; }}
.heatmap-wrap .hm-1 {{ background: #fef3c7; }}
.heatmap-wrap .hm-2 {{ background: #fde68a; }}
.heatmap-wrap .hm-3 {{ background: #fbbf24; color: #78350f; }}
.heatmap-wrap .hm-4 {{ background: #f59e0b; color: #fff; font-weight: 600; }}
.heatmap-wrap .hm-5 {{ background: #d97706; color: #fff; font-weight: 700; }}
.heatmap-wrap .hm-clickable {{ cursor: pointer; }}
.heatmap-wrap .hm-clickable:hover {{ outline: 2px solid #1e40af; outline-offset: -2px; z-index: 2; position: relative; }}

/* MIX 内訳モーダル */
#mix-detail-modal {{
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(15, 23, 42, 0.5); z-index: 1000; align-items: center; justify-content: center;
}}
#mix-detail-modal.open {{ display: flex; }}
#mix-detail-modal .mix-detail-card {{
  background: #fff; border-radius: 8px; padding: 20px 24px; max-width: 640px; width: 90%;
  box-shadow: 0 20px 40px rgba(0,0,0,0.2); max-height: 80vh; overflow-y: auto;
}}
#mix-detail-modal .mix-detail-title {{
  font-weight: 600; color: #0f172a; font-size: 14px; padding-bottom: 12px;
  border-bottom: 2px solid #e5e7eb; margin-bottom: 12px;
}}
#mix-detail-modal .mix-detail-body {{
  display: flex; flex-wrap: wrap; gap: 8px;
}}
#mix-detail-modal .mix-detail-body .badge {{
  background: #fef3c7; color: #78350f; padding: 6px 12px; border-radius: 12px;
  font-size: 13px; font-weight: 500;
}}
#mix-detail-modal .mix-detail-close {{
  margin-top: 16px; padding: 8px 16px; background: #3b82f6; color: #fff;
  border: none; border-radius: 6px; cursor: pointer; font-size: 13px;
}}
</style>
<script>
function showMixDetail(el) {{
  var title = el.getAttribute('data-title') || '';
  var breakdown = el.getAttribute('data-breakdown') || '';
  var modal = document.getElementById('mix-detail-modal');
  document.getElementById('mix-detail-title').textContent = title;
  var body = document.getElementById('mix-detail-body');
  body.innerHTML = '';
  if (!breakdown || breakdown === '(データなし)') {{
    body.innerHTML = '<span style="color:#94a3b8;font-size:13px;">(データなし)</span>';
  }} else {{
    breakdown.split(/\\s+/).forEach(function(part) {{
      if (!part) return;
      var span = document.createElement('span');
      span.className = 'badge';
      span.textContent = part;
      body.appendChild(span);
    }});
  }}
  modal.classList.add('open');
}}
function closeMixDetail() {{
  document.getElementById('mix-detail-modal').classList.remove('open');
}}
document.addEventListener('DOMContentLoaded', function() {{
  document.getElementById('mix-detail-modal').addEventListener('click', function(e) {{
    if (e.target === this) closeMixDetail();
  }});
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') closeMixDetail();
  }});
}});
</script>

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

<div class="kpi-section-title">調剤報酬明細 ({main_label} 累計)</div>
<div class="receipt">
  <table>
    <tr class="cat-header"><td colspan="2">■ 調剤技術料</td></tr>
    <tr><td class="item-name">調剤基本料 (+地域加算)<span class="item-code">[7]</span></td>
        <td class="num">{t_dispensing_base:,}</td></tr>
    <tr><td class="item-name">調剤料<span class="item-code">[2]</span></td>
        <td class="num">{t_dispensing_fee_total:,}</td></tr>
    <tr><td class="item-name">調剤加算 (計量混合加算等)<span class="item-code">[8]</span></td>
        <td class="num">{t_dispensing_add:,}</td></tr>
    <tr class="subtotal"><td style="padding-left:24px">小　計</td>
        <td class="num">{t_tech_subtotal:,}</td></tr>

    <tr class="cat-header"><td colspan="2">■ 薬学管理料</td></tr>
    <tr><td class="item-name">服薬管理指導料<span class="item-code">[9]</span></td>
        <td class="num">{t_drug_guidance:,}</td></tr>
    <tr><td class="item-name">その他 (調剤管理料・特薬管加算等)<span class="item-code">[11]</span></td>
        <td class="num">{t_pharmacy_mgmt_other:,}</td></tr>
    <tr class="subtotal"><td style="padding-left:24px">小　計<span class="item-code">[3]</span></td>
        <td class="num">{t_pharmacy_mgmt_fee_total:,}</td></tr>

    <tr class="cat-header"><td colspan="2">■ 薬剤料</td></tr>
    <tr class="subtotal"><td style="padding-left:24px">合　計<span class="item-code">[1]</span></td>
        <td class="num">{t_drug_fee:,}</td></tr>

    <tr class="grand"><td>合　計 (請求点数)<span class="item-code" style="color:#94a3b8">[5]</span></td>
        <td class="num">{t_total_points:,} 点</td></tr>
    <tr class="copay"><td>患者負担金 (保険内)<span class="item-code" style="color:#a16207">[13]</span></td>
        <td class="num">{t_patient_copay:,} 円</td></tr>
    {senteryoyo_row}
    <tr class="copay"><td>総患者負担額<span class="item-code" style="color:#a16207">[17]</span></td>
        <td class="num">{t_patient_copay_total:,} 円</td></tr>
  </table>
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
<thead><tr><th>YJコード</th><th>薬品名 / 一般名</th><th class="num">回数</th><th class="num">総数量</th><th>単位</th><th class="num">薬価</th></tr></thead>
<tbody>
{drug_rows}
</tbody>
</table>

<h2>混合処方 (外用剤の計量混合) 集計</h2>

<h3 style="font-size:14px;margin:20px 0 8px;color:#475569;">週次トレンド (直近 26 週)</h3>
<div class="chart-wrap">{weekly_chart}</div>

<details style="margin:24px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">📊 予製計画レポート — 年度対比 (件数上位 50 組合せ) — クリックで展開</summary>
<div style="margin-top:12px;">
{planning_report}
<div style="overflow-x:auto;">
<table style="font-size:12px;">
<thead>
  <tr>
    <th rowspan="2">組合せ</th>
    <th rowspan="2" class="num">累計<br>件数</th>
    <th rowspan="2" class="num">累計<br>量</th>
    <th colspan="2" class="num" style="background:#f0fdf4;">📅 今月 / 前年同月</th>
    <th colspan="2" class="num" style="background:#eff6ff;">📅 今週 / 前年同週</th>
    <th rowspan="2" class="num" style="background:#f5f3ff;">月平均<br>(今年度)</th>
    <th rowspan="2" class="num" style="background:#fef2f2;">予製推奨<br>2週分</th>
    <th rowspan="2" style="background:#fef3c7;">MIX 量 別内訳<br><span style="font-weight:normal;font-size:10px;color:#78350f;">前年同月分</span></th>
  </tr>
  <tr>
    <th class="num" style="background:#f0fdf4;font-size:10px;color:#059669;">今月</th>
    <th class="num" style="background:#fef3c7;font-size:10px;color:#78350f;">前年同月</th>
    <th class="num" style="background:#eff6ff;font-size:10px;color:#1e40af;">今週</th>
    <th class="num" style="background:#fef3c7;font-size:10px;color:#78350f;">前年同週</th>
  </tr>
</thead>
<tbody>
{planning_rows}
</tbody>
</table>
</div>
</div>
</details>

<details open style="margin:24px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">📆 週別ドリルダウン (Top10 コンボ × 直近8週 × 前年同週対比)</summary>
<p style="color:#64748b;font-size:12px;margin-top:8px;">セル上段 = 今週の件数、下段小さい数字 = 総量。「前:N件 Xg」= 前年同週の実績。</p>
<div style="overflow-x:auto;">
<table style="border-collapse:collapse;font-size:11px;">
<thead><tr><th style="background:#f1f5f9;padding:6px 8px;">組合せ</th>{weekly_drill_headers}</tr></thead>
<tbody>
{weekly_drill_rows}
</tbody>
</table>
</div>
</details>

<details style="margin:24px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">🗓 月別ヒートマップ (Top30 組合せ × 全月) — 単位: g / 予製計画向け — クリックで展開</summary>
<p style="color:#64748b;font-size:12px;margin-top:8px;">セル = その月に混合された<strong>総量 (g)</strong>。1000g 以上は k 表示 (例: 1.5k = 1,500g)。色濃さは同コンボの月平均量に対する相対強度。セルにカーソルを乗せると詳細表示。</p>
<div class="heatmap-wrap">
<table>
<thead><tr><th class="hm-label" style="background:#f1f5f9;">組合せ</th>{month_headers}<th class="num" style="background:#f1f5f9;">合計</th></tr></thead>
<tbody>
{heatmap_rows}
</tbody>
</table>
</div>
</details>

<details style="margin:24px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">混合組合せ 累計 (上位 30) — クリックで展開</summary>
<p style="color:#64748b;font-size:12px;margin-top:8px;">record 3 field 5 が「混合」の RP + 外用剤 (M/N/Q/X/U/P) の組合せのみ集計。MIX 量 = 同 RP 内の外用剤 総処方量の合計。「30g × 5件」= 合計 30g の混合が 5 回。</p>
<table>
<thead><tr><th class="num">総件数</th><th>混合された薬剤の組合せ</th><th>MIX 量別 内訳 (量 × 件数)</th></tr></thead>
<tbody>
{mix_combos}
</tbody>
</table>
</details>

<h2>各種加算・料金 累計</h2>
<table>
<thead><tr><th>種別</th><th>加算コード</th><th>加算名</th><th class="num">算定回数</th><th class="num">合計点数</th></tr></thead>
<tbody>
{fee_rows}
</tbody>
</table>

<style>
.raw-details {{ margin: 8px 0; padding: 8px; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; }}
.raw-details summary {{ cursor: pointer; font-weight: 500; padding: 4px 0; }}
.raw-details summary:hover {{ color: #0369a1; }}
.raw-details pre {{ background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; margin-top: 8px; font-family: "SFMono-Regular", Menlo, Consolas, monospace; }}
.meta {{ color: #64748b; font-size: 12px; margin-left: 12px; }}
</style>
<details style="margin:24px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">最新受入 生データ (直近 20 件、行をクリックで raw 展開) — クリックで展開</summary>
<div style="margin-top:12px;">
{recent_rows}
</div>
</details>

<details style="margin:16px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;">
<summary style="cursor:pointer;font-weight:600;font-size:14px;color:#0f172a;">成分別 累計 (YJ 1〜7 桁: 同一成分でまとめる) — クリックで展開</summary>
<p style="color:#64748b;font-size:12px;margin-top:8px;">同じ成分の 先発品・後発品・別剤形をまとめて集計。品目数 &gt; 1 は同じ成分の複数バリエーションが処方された = 後発切替検討や剤形選択の余地あり。</p>
<table>
<thead><tr><th>YJ (1-7)</th><th>成分 (代表薬品名 / 一般名)</th><th class="num">品目数</th><th class="num">調剤回数</th><th class="num">総数量</th></tr></thead>
<tbody>
{ingredient_rows}
</tbody>
</table>
</details>

<div class="updated">最終更新: {now} (10分ごとに自動再読込)</div>

<div id="mix-detail-modal">
  <div class="mix-detail-card">
    <div class="mix-detail-title" id="mix-detail-title"></div>
    <div class="mix-detail-body" id="mix-detail-body"></div>
    <button class="mix-detail-close" onclick="closeMixDetail()">閉じる</button>
  </div>
</div>
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

    # ---- HTML キャッシュ (期間別、60 秒 TTL、行数変化で invalidate) ----
    import time as _time_mod
    global _DASHBOARD_CACHE
    try:
        _DASHBOARD_CACHE
    except NameError:
        _DASHBOARD_CACHE = {}
    row_state = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM prescriptions").fetchone()
    cache_key = (period or "all", row_state[0], row_state[1])
    cached = _DASHBOARD_CACHE.get(cache_key)
    if cached and (_time_mod.time() - cached[0]) < 60:
        return HTMLResponse(content=cached[1])

    # ---- 期間フィルタ ----
    from datetime import datetime, timedelta
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
    this_month = now.strftime("%Y%m")

    # picker で選ばれた ISO 形式 (YYYY-MM-DD / YYYY-MM) も受け付ける
    picker_date_val = None  # 明示的な日付選択
    picker_month_val = None  # 明示的な月選択
    if period == "today":
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{today_str}'"
        period_label = f"今日 ({now:%Y-%m-%d})"
    elif period == "yesterday":
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{yesterday_str}'"
        period_label = f"昨日 ({(now - timedelta(days=1)):%Y-%m-%d})"
    elif period == "month":
        period_where = f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{this_month}'"
        period_label = f"今月 ({now:%Y-%m})"
    elif period and len(period) == 10 and period[4] == "-" and period[7] == "-":
        # YYYY-MM-DD (input type=date)
        ymd = period.replace("-", "")
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{ymd}'"
        period_label = f"{period}"
        picker_date_val = period
    elif period and len(period) == 7 and period[4] == "-":
        # YYYY-MM (input type=month)
        ym = period.replace("-", "")
        period_where = f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{ym}'"
        period_label = f"{period}"
        picker_month_val = period
    elif period and len(period) == 8 and period.isdigit():
        # YYYYMMDD (旧形式互換)
        period_where = f"COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)) = '{period}'"
        period_label = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    elif period and len(period) == 6 and period.isdigit():
        period_where = f"SUBSTR(COALESCE(dispense_date, STRFTIME('%Y%m%d', detected_at)), 1, 6) = '{period}'"
        period_label = f"{period[:4]}-{period[4:6]}"
    else:
        period_where = "1=1"
        period_label = "全期間"
        period = "all"

    prescription_count = conn.execute(f"SELECT COUNT(*) FROM prescriptions WHERE {period_where}").fetchone()[0]
    # period=all の時は prescriptions JOIN 不要 (drugs 単独で高速集計)
    is_all_period = (period_where.strip() == "1=1")

    if is_all_period:
        drug_kinds = conn.execute(
            "SELECT COUNT(DISTINCT yj_code) FROM drugs WHERE yj_code IS NOT NULL"
        ).fetchone()[0]
        drug_agg = conn.execute(
            """SELECT yj_code, COUNT(*) AS n, SUM(total_quantity) AS qty,
                       SUM(CASE WHEN total_quantity IS NOT NULL THEN 1 ELSE 0 END) AS valid_n
                FROM drugs
                WHERE yj_code IS NOT NULL
                GROUP BY yj_code ORDER BY n DESC, qty DESC LIMIT 50"""
        ).fetchall()
    else:
        drug_kinds = conn.execute(
            f"SELECT COUNT(DISTINCT d.yj_code) FROM drugs d JOIN prescriptions p ON d.prescription_id = p.id WHERE d.yj_code IS NOT NULL AND {period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')}"
        ).fetchone()[0]
        drug_agg = conn.execute(
            f"""SELECT d.yj_code, COUNT(*) AS n, SUM(d.total_quantity) AS qty,
                       SUM(CASE WHEN d.total_quantity IS NOT NULL THEN 1 ELSE 0 END) AS valid_n
                FROM drugs d
                JOIN prescriptions p ON d.prescription_id = p.id
                WHERE d.yj_code IS NOT NULL AND {period_where.replace('dispense_date', 'p.dispense_date').replace('detected_at', 'p.detected_at')}
                GROUP BY d.yj_code ORDER BY n DESC, qty DESC LIMIT 50"""
        ).fetchall()
    # TOP50 の YJ コードだけ詳細情報を IN で引き当て
    top_yjs = [r["yj_code"] for r in drug_agg if r["yj_code"]]
    detail_map: dict[str, dict] = {}
    if top_yjs:
        placeholders = ",".join("?" * len(top_yjs))
        details = conn.execute(
            f"""SELECT d.yj_code,
                       MAX(COALESCE(dm.name, d.name)) AS drug_name,
                       MAX(COALESCE(dm.unit, d.unit)) AS drug_unit,
                       MAX(dm.usage_category) AS usage_category,
                       MAX(d.form) AS client_form,
                       MAX(dm.unit_price) AS master_price,
                       MAX(dm.generic_name) AS generic_name
                FROM drugs d
                LEFT JOIN drug_master dm ON d.yj_code = dm.yj_code
                WHERE d.yj_code IN ({placeholders})
                GROUP BY d.yj_code""",
            top_yjs,
        ).fetchall()
        for d in details:
            detail_map[d["yj_code"]] = d
    def _detail(yj, col):
        d = detail_map.get(yj)
        return d[col] if d is not None else None
    drug_data = [
        {
            "yj_code": r["yj_code"], "n": r["n"], "qty": r["qty"], "valid_n": r["valid_n"],
            "drug_name": _detail(r["yj_code"], "drug_name"),
            "drug_unit": _detail(r["yj_code"], "drug_unit"),
            "usage_category": _detail(r["yj_code"], "usage_category"),
            "client_form": _detail(r["yj_code"], "client_form"),
            "master_price": _detail(r["yj_code"], "master_price"),
            "generic_name": _detail(r["yj_code"], "generic_name"),
        }
        for r in drug_agg
    ]

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
            f'<tr><td>{_h(r["yj_code"])}</td>'
            f'<td>{_h(r["drug_name"])}{generic}</td>'
            f'<td class="num">{r["n"]}</td>'
            f'<td class="num">{qty_display}</td>'
            f'<td>{_h(r["drug_unit"])}</td>'
            f'<td class="num">{price}</td></tr>'
        )
    drug_rows_html = "\n".join(_drug_row(r) for r in drug_data) or '<tr><td colspan="6">(データなし)</td></tr>'

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
             COALESCE(SUM(drug_fee), 0) AS drug_fee,
             COALESCE(SUM(dispensing_fee_total), 0) AS dispensing_fee_total,
             COALESCE(SUM(pharmacy_mgmt_fee_total), 0) AS pharmacy_mgmt_fee_total,
             COALESCE(SUM(dispensing_base_fee), 0) AS dispensing_base,
             COALESCE(SUM(dispensing_add_fee), 0) AS dispensing_add,
             COALESCE(SUM(drug_guidance_fee), 0) AS drug_guidance,
             COALESCE(SUM(pharmacy_mgmt_other), 0) AS pharmacy_mgmt_other,
             COALESCE(SUM(patient_copay_total), 0) AS patient_copay_total,
             COALESCE(SUM(CASE WHEN COALESCE(patient_copay_total, 0) > COALESCE(patient_copay, 0)
                               THEN patient_copay_total - patient_copay ELSE 0 END), 0) AS senteryoyo_total,
             COALESCE(SUM(CASE WHEN COALESCE(patient_copay_total, 0) > COALESCE(patient_copay, 0)
                               THEN 1 ELSE 0 END), 0) AS senteryoyo_count,
             COALESCE(SUM(senteryoyo_fee_excl_tax), 0) AS senteryoyo_excl_tax,
             COALESCE(SUM(senteryoyo_tax), 0) AS senteryoyo_tax
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

    # ---- 年度計算 (4月始まり) ----
    _today = datetime.now().date()
    _cy = _today.year if _today.month >= 4 else _today.year - 1
    _py = _cy - 1
    cy_start = f"{_cy}0401"
    cy_end = f"{_cy+1}0331"
    py_start = f"{_py}0401"
    py_end = f"{_py+1}0331"
    # 今年度の経過月数 (前年比の月平均計算用)
    _cy_end_date = _today if _today.year == _cy else datetime(_cy + 1, 3, 31).date()
    _cy_months = max(1, (_cy_end_date.year - _cy) * 12 + _cy_end_date.month - 4 + 1)

    # 前年同月の期間 (YYYYMMDD 文字列で比較用) — combos_data ループより前に定義必須
    prev_ym_start = f"{_today.year - 1}{_today.month:02d}01"
    from calendar import monthrange as _mr
    _last_day = _mr(_today.year - 1, _today.month)[1]
    prev_ym_end = f"{_today.year - 1}{_today.month:02d}{_last_day:02d}"

    # 混合処方: combo × 量 で集計 → Python で combo ごとに内訳を組立
    combos_data = conn.execute(
        f"""
        WITH mix AS (
          SELECT r.id AS rp_id,
                 p.dispense_date,
                 GROUP_CONCAT(d.name, ' + ') AS combo,
                 ROUND(SUM(COALESCE(d.quantity, 0)), 2) AS mix_qty,
                 MAX(d.unit) AS unit
          FROM rps r
          JOIN drugs d ON d.prescription_id = r.prescription_id AND d.rp_no = r.rp_no
          JOIN prescriptions p ON p.id = r.prescription_id
          WHERE r.is_mixed = 1
            AND r.site_text = '混合'
            AND d.name IS NOT NULL
            AND d.form = '外用'
          GROUP BY r.id
        )
        SELECT combo, mix_qty, unit, dispense_date, COUNT(*) AS n
        FROM mix
        WHERE combo IS NOT NULL
        GROUP BY combo, mix_qty, unit, dispense_date
        ORDER BY combo, mix_qty
        """
    ).fetchall()

    # ---- combo ごとの多角的集計 (年度/月/週) ----
    from collections import defaultdict as _dd
    from datetime import date as _date
    combo_summary: dict = _dd(lambda: {
        "total_n": 0, "total_qty": 0.0, "unit": "",
        "cy_n": 0, "cy_qty": 0.0,
        "py_n": 0, "py_qty": 0.0,
        "qty_hist_prev_month": _dd(int),  # mix_qty (g) → 件数 (前年同月のみ)
        "monthly": _dd(lambda: {"n": 0, "qty": 0.0}),  # "YYYY-MM" → {n, qty}
        "weekly": _dd(lambda: {"n": 0, "qty": 0.0}),   # "YYYY-Wnn" → {n, qty}
        "monthly_qty_hist": _dd(lambda: _dd(int)),  # "YYYY-MM" → {mix_qty: 件数}
    })
    for r in combos_data:
        c = r["combo"]
        n = r["n"]
        qty = (r["mix_qty"] or 0.0) * n
        dd_ = r["dispense_date"]
        s = combo_summary[c]
        s["total_n"] += n
        s["total_qty"] += qty
        s["unit"] = r["unit"] or s["unit"]
        # MIX 量別内訳は 前年同月分のみ集計 (予製計画立案用)
        if dd_ and prev_ym_start <= dd_ <= prev_ym_end:
            s["qty_hist_prev_month"][r["mix_qty"] or 0.0] += n
        if dd_ and cy_start <= dd_ <= cy_end:
            s["cy_n"] += n
            s["cy_qty"] += qty
        elif dd_ and py_start <= dd_ <= py_end:
            s["py_n"] += n
            s["py_qty"] += qty
        # 月別 / 週別
        if dd_ and len(dd_) >= 8:
            try:
                y, m, d = int(dd_[:4]), int(dd_[4:6]), int(dd_[6:8])
                ym = f"{y}-{m:02d}"
                s["monthly"][ym]["n"] += n
                s["monthly"][ym]["qty"] += qty
                s["monthly_qty_hist"][ym][r["mix_qty"] or 0.0] += n
                iso = _date(y, m, d).isocalendar()
                wk = f"{iso[0]}-{iso[1]:02d}"
                s["weekly"][wk]["n"] += n
                s["weekly"][wk]["qty"] += qty
            except (ValueError, TypeError):
                pass

    # 今月・前年同月 / 今週・前年同週 のキー
    this_month_key = f"{_today.year}-{_today.month:02d}"
    prev_year_same_month = f"{_today.year - 1}-{_today.month:02d}"
    _iso_now = _today.isocalendar()
    this_week_key = f"{_iso_now[0]}-{_iso_now[1]:02d}"
    prev_year_same_week = f"{_iso_now[0] - 1}-{_iso_now[1]:02d}"

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
        agg = defaultdict(int)  # (qty, unit) → n
        for qty, unit, n in entries:
            agg[(qty, unit)] += n
        rows = sorted(agg.items(), key=lambda x: -(x[0][0] or 0))
        parts = []
        for (qty, unit), n in rows:
            qty_str = f"{qty:g}" if qty else "0"
            parts.append(f'<span style="display:inline-block;background:#e0f2fe;border-radius:12px;padding:2px 10px;margin:2px 4px 2px 0;font-size:12px;">{qty_str}{_h(unit or "")}×{n}</span>')
        return "".join(parts)

    mix_combos_html = "\n".join(
        f'<tr><td class="num"><strong>{combo_total[c]}</strong></td>'
        f'<td>{_h(c)}</td>'
        f'<td>{_fmt_breakdown(combo_map[c])}</td></tr>'
        for c in sorted_combos
    ) or '<tr><td colspan="3">(データなし — 新クライアントから RP 情報 (is_mixed) が届き次第表示)</td></tr>'

    # ---- 予製計画 レポート HTML: 同月同週対比 + 総量 + 月平均 + 予製推奨 ----
    def _fmt_qty(qty, unit):
        if not qty:
            return "0"
        if qty >= 1000:
            return f"{qty/1000:.1f}k{unit}"
        return f"{qty:.0f}{unit}"

    def _fmt_period_cell(cur, prev, unit, bg_cur, bg_prev):
        """今期/前期のセル: 件数上 + 量下、色分け背景"""
        cur_n = cur.get("n", 0)
        cur_q = cur.get("qty", 0.0)
        prev_n = prev.get("n", 0)
        prev_q = prev.get("qty", 0.0)
        # 増減アイコン
        if prev_n or prev_q:
            diff_pct = ((cur_q - prev_q) / prev_q * 100) if prev_q else 0
            arrow = "↑" if diff_pct > 10 else ("↓" if diff_pct < -10 else "→")
            arrow_color = "#059669" if diff_pct > 10 else ("#dc2626" if diff_pct < -10 else "#94a3b8")
        else:
            arrow = "🆕" if cur_n else "—"
            arrow_color = "#dc2626"
        return (
            f'<td class="num" style="background:{bg_cur};padding:4px 6px;">'
            f'<div style="font-weight:600;">{cur_n}件</div>'
            f'<div style="font-size:10px;color:#334155;">{_fmt_qty(cur_q, unit)}</div>'
            f'</td>'
            f'<td class="num" style="background:{bg_prev};padding:4px 6px;">'
            f'<div style="color:#78350f;">{prev_n}件</div>'
            f'<div style="font-size:10px;color:#a16207;">{_fmt_qty(prev_q, unit)}</div>'
            f'<div style="font-size:10px;color:{arrow_color};font-weight:600;margin-top:2px;">{arrow}</div>'
            f'</td>'
        )

    planning_sorted = sorted(combo_summary.items(), key=lambda x: -x[1]["cy_qty"] if x[1]["cy_qty"] else -x[1]["total_qty"])[:50]

    def _planning_row(combo, s):
        unit = s["unit"] or "g"
        monthly_avg_qty = s["cy_qty"] / _cy_months if _cy_months else 0
        yosei_recommend = monthly_avg_qty * 0.5
        all_qtys = sorted(s["qty_hist_prev_month"].items(), key=lambda x: -x[1])
        if all_qtys:
            qty_dist = " ".join(f'<span style="background:#fef3c7;color:#78350f;padding:1px 6px;border-radius:8px;font-size:11px;display:inline-block;margin:1px 2px;">{q:g}{unit}×{n}</span>' for q, n in all_qtys if q)
        else:
            qty_dist = '<span style="color:#94a3b8;font-size:11px;">前年同月データなし</span>'
        # 今月/前年同月 & 今週/前年同週
        cm = s["monthly"].get(this_month_key, {"n": 0, "qty": 0.0})
        pm = s["monthly"].get(prev_year_same_month, {"n": 0, "qty": 0.0})
        cw = s["weekly"].get(this_week_key, {"n": 0, "qty": 0.0})
        pw = s["weekly"].get(prev_year_same_week, {"n": 0, "qty": 0.0})
        return (
            f'<tr>'
            f'<td style="font-size:12px;">{_h(combo)}</td>'
            f'<td class="num">{s["total_n"]:,}</td>'
            f'<td class="num" style="color:#334155">{_fmt_qty(s["total_qty"], unit)}</td>'
            + _fmt_period_cell(cm, pm, unit, "#f0fdf4", "#fef3c7")  # 今月/前年同月
            + _fmt_period_cell(cw, pw, unit, "#eff6ff", "#fef3c7")  # 今週/前年同週
            + f'<td class="num" style="background:#f5f3ff"><strong>{_fmt_qty(monthly_avg_qty, unit)}</strong></td>'
            f'<td class="num" style="background:#fef2f2"><strong style="color:#991b1b">{_fmt_qty(yosei_recommend, unit)}</strong></td>'
            f'<td>{qty_dist}</td>'
            f'</tr>'
        )
    planning_rows_html = "\n".join(_planning_row(c, s) for c, s in planning_sorted) or '<tr><td colspan="11">(データなし)</td></tr>'

    planning_report_html = (
        f'<div style="background:#f8fafc;padding:10px 14px;border-radius:6px;margin:12px 0;font-size:12px;color:#475569;">'
        f'📅 今月 = <strong>{this_month_key}</strong> vs 前年同月 <strong>{prev_year_same_month}</strong> &nbsp;|&nbsp; '
        f'今週 = <strong>{this_week_key}</strong> vs 前年同週 <strong>{prev_year_same_week}</strong><br>'
        f'月平均 = 今年度 ({_cy}年度) 経過 {_cy_months}ヶ月の平均 &nbsp;|&nbsp; 予製推奨量 = 月平均量 × 0.5 (2週間分)'
        f'</div>'
    )

    # ---- 週別ドリルダウン: Top10 コンボ × 直近8週 × 前年同週対比 ----
    from datetime import timedelta as _td
    def _week_str_from_offset(back_weeks):
        target = _today - _td(weeks=back_weeks)
        iso = target.isocalendar()
        return f"{iso[0]}-{iso[1]:02d}"

    recent_weeks = [_week_str_from_offset(i) for i in range(8)][::-1]  # 古い→新しい
    prev_year_weeks = [f"{int(w[:4])-1}-{w[5:]}" for w in recent_weeks]

    top10_combos_wk = sorted(combo_summary.items(), key=lambda x: -x[1]["cy_qty"] if x[1]["cy_qty"] else -x[1]["total_qty"])[:10]

    def _wk_cell(cur, prev, unit):
        cur_n = cur.get("n", 0)
        cur_q = cur.get("qty", 0.0)
        prev_n = prev.get("n", 0)
        prev_q = prev.get("qty", 0.0)
        cur_disp = f'{cur_n}<div style="font-size:9px;color:#059669">{_fmt_qty(cur_q, unit)}</div>' if cur_n else '—'
        prev_disp = f'<div style="font-size:9px;color:#78350f">前:{prev_n}件 {_fmt_qty(prev_q, unit)}</div>' if prev_n else ''
        return f'<td style="text-align:center;padding:4px 6px;background:{"#f0fdf4" if cur_n else "#fff"};">{cur_disp}{prev_disp}</td>'

    def _weekly_row(combo, s):
        unit = s["unit"] or "g"
        cells = "".join(
            _wk_cell(s["weekly"].get(recent_weeks[i], {"n":0,"qty":0.0}),
                     s["weekly"].get(prev_year_weeks[i], {"n":0,"qty":0.0}), unit)
            for i in range(8)
        )
        combo_short = combo if len(combo) < 40 else combo[:38] + "…"
        return f'<tr><td style="font-size:11px;padding:4px 8px;background:#f8fafc;">{_h(combo_short)}</td>{cells}</tr>'

    weekly_drilldown_headers = "".join(
        f'<th style="font-size:10px;padding:4px;background:#f1f5f9;">{recent_weeks[i][2:4]}W{recent_weeks[i][5:]}</th>'
        for i in range(8)
    )
    weekly_drilldown_rows = "\n".join(_weekly_row(c, s) for c, s in top10_combos_wk) or f'<tr><td colspan="9">(データなし)</td></tr>'

    # ---- 月別ヒートマップ: combo × month の件数 ----
    monthly_combo_map: dict = _dd(lambda: _dd(lambda: {"n": 0, "qty": 0.0}))
    all_months: set = set()
    for r in combos_data:
        c = r["combo"]
        n = r["n"]
        qty = (r["mix_qty"] or 0.0) * n
        dd_ = r["dispense_date"]
        if not dd_ or len(dd_) < 6:
            continue
        ym = dd_[:6]
        all_months.add(ym)
        monthly_combo_map[c][ym]["n"] += n
        monthly_combo_map[c][ym]["qty"] += qty
    sorted_months = sorted(all_months)  # 全期間
    # Top 30 combos by 累計件数
    heatmap_combos = sorted(combo_summary.items(), key=lambda x: -x[1]["total_n"])[:30]

    def _heat_cell(val, max_val, unit, combo, ym, qty_hist, n_count):
        if not val:
            return '<td class="hm-cell hm-0"></td>'
        intensity = min(1.0, val / max(max_val, 1))
        if intensity >= 0.8: cls = "hm-5"
        elif intensity >= 0.6: cls = "hm-4"
        elif intensity >= 0.4: cls = "hm-3"
        elif intensity >= 0.2: cls = "hm-2"
        else: cls = "hm-1"
        if val >= 1000:
            display = f"{val/1000:.1f}k"
        else:
            display = f"{val:.0f}"
        # 量パターン内訳を data 属性に (クリックで popover 表示)
        top_patterns = sorted(qty_hist.items(), key=lambda x: -x[1])
        breakdown_parts = [f"{q:g}{unit}×{c}" for q, c in top_patterns if q]
        breakdown_str = "  ".join(breakdown_parts) if breakdown_parts else "(データなし)"
        # クリック時 タイトル行に表示するデータ
        title_line = f"{combo} ／ {ym} ／ 総量 {val:.1f}{unit} ／ {n_count}件"
        # HTML escape
        combo_esc = html_lib.escape(combo)
        ym_esc = html_lib.escape(ym)
        bd_esc = html_lib.escape(breakdown_str)
        title_esc = html_lib.escape(title_line)
        return (
            f'<td class="hm-cell hm-clickable {cls}" '
            f'data-title="{title_esc}" '
            f'data-breakdown="{bd_esc}" '
            f'onclick="showMixDetail(this)">{display}</td>'
        )

    def _heat_row(combo, s):
        unit = s["unit"] or "g"
        cells = []
        for ym in sorted_months:
            entry = monthly_combo_map[combo].get(ym, {"n": 0, "qty": 0.0})
            v = entry.get("qty", 0.0)
            n_count = entry.get("n", 0)
            qty_hist = s["monthly_qty_hist"].get(ym, {})
            cells.append(_heat_cell(v, s["total_qty"] / max(1, len(sorted_months)) * 3, unit, combo, ym, qty_hist, n_count))
        combo_short = combo if len(combo) < 40 else combo[:38] + "…"
        total_disp = f"{s['total_qty']/1000:.1f}k{unit}" if s["total_qty"] >= 1000 else f"{s['total_qty']:.0f}{unit}"
        return f'<tr><td class="hm-label">{_h(combo_short)}</td>{"".join(cells)}<td class="num" style="background:#f8fafc;font-weight:600">{total_disp}</td></tr>'

    month_header_html = "".join(f'<th class="hm-month">{m[:4]}<br>/{m[4:6]}</th>' for m in sorted_months)
    heatmap_rows_html = "\n".join(_heat_row(c, s) for c, s in heatmap_combos) or f'<tr><td colspan="{len(sorted_months)+2}">(データなし)</td></tr>'

    mix_total = conn.execute(
        "SELECT COUNT(DISTINCT prescription_id) FROM fees WHERE is_mix_flag = 1"
    ).fetchone()[0]

    # 全期間の 計量混合 総件数 (期間フィルタなし = 全期間)
    mix_total_all = conn.execute(
        "SELECT COUNT(DISTINCT prescription_id) FROM fees WHERE is_mix_flag = 1"
    ).fetchone()[0]

    # 週次集計: 混合発生 prescription の dispense_date だけ取得 (小さい集合) →
    # Python で ISO 週に変換して集計
    mix_dates = conn.execute(
        """SELECT p.dispense_date
           FROM prescriptions p
           JOIN fees f ON f.prescription_id = p.id
           WHERE f.is_mix_flag = 1 AND p.dispense_date IS NOT NULL"""
    ).fetchall()
    from datetime import date as _date
    from collections import Counter as _Counter
    _weekly_counter: _Counter = _Counter()
    for r in mix_dates:
        d = r["dispense_date"]
        if not d or len(d) < 8:
            continue
        try:
            dt = _date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            iso = dt.isocalendar()
            _weekly_counter[f"{iso[0]}-{iso[1]:02d}"] += 1
        except ValueError:
            continue
    sorted_weeks = sorted(_weekly_counter.items())[-26:]  # 直近 26 週
    mix_weekly = [{"wk": wk, "n": n} for wk, n in sorted_weeks]

    # 直近12週で 平均 + 先週件数
    recent12 = mix_weekly[-12:] if len(mix_weekly) >= 12 else mix_weekly
    mix_weekly_avg = sum(r["n"] for r in recent12) / len(recent12) if recent12 else 0.0
    mix_last_week = mix_weekly[-1]["n"] if mix_weekly else 0

    # SVG バー チャート生成
    def _weekly_chart_svg(weeks):
        if not weeks:
            return '<div style="color:#94a3b8;font-size:13px;padding:20px;">データなし</div>'
        max_n = max(r["n"] for r in weeks) or 1
        bar_w = 24
        bar_gap = 6
        left_pad = 40
        top_pad = 20
        chart_h = 180
        bottom_pad = 40
        width = left_pad + len(weeks) * (bar_w + bar_gap) + 10
        height = top_pad + chart_h + bottom_pad
        parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
        # グリッド線 (4分割)
        for i in range(5):
            y = top_pad + chart_h - (chart_h * i / 4)
            val = int(max_n * i / 4)
            parts.append(f'<line class="grid" x1="{left_pad}" y1="{y:.1f}" x2="{width-10}" y2="{y:.1f}"/>')
            parts.append(f'<text x="{left_pad-4}" y="{y+3:.1f}" style="font-size:9px;fill:#64748b;text-anchor:end;">{val}</text>')
        # 各バー
        for i, r in enumerate(weeks):
            x = left_pad + i * (bar_w + bar_gap)
            h = (r["n"] / max_n) * chart_h if max_n > 0 else 0
            y = top_pad + chart_h - h
            wk = r["wk"] or ""
            n = r["n"]
            parts.append(f'<rect class="bar" x="{x}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}"><title>{wk}: {n}件</title></rect>')
            # ラベル (週番号のみ)
            if wk and "-" in wk:
                yr, w = wk.split("-", 1)
                lbl = f"{yr[2:]}W{w}"
            else:
                lbl = wk
            parts.append(f'<text class="lbl" x="{x + bar_w/2:.1f}" y="{top_pad + chart_h + 12}" transform="rotate(-45 {x + bar_w/2:.1f} {top_pad + chart_h + 12})">{lbl}</text>')
            # 値ラベル (件数 5 以上のときのみ表示、上に)
            if n >= 5:
                parts.append(f'<text class="val" x="{x + bar_w/2:.1f}" y="{y-3:.1f}">{n}</text>')
        # X 軸
        parts.append(f'<line class="axis" x1="{left_pad}" y1="{top_pad + chart_h}" x2="{width-10}" y2="{top_pad + chart_h}"/>')
        parts.append('</svg>')
        return "".join(parts)

    weekly_chart_svg = _weekly_chart_svg(mix_weekly)
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
        f'<details class="raw-details"><summary>#{r["id"]} '
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
        mix_total_all=mix_total_all,
        mix_weekly_avg=mix_weekly_avg,
        mix_last_week=mix_last_week,
        weekly_chart=weekly_chart_svg,
        mix_combos=mix_combos_html,
        planning_report=planning_report_html,
        planning_rows=planning_rows_html,
        month_headers=month_header_html,
        heatmap_rows=heatmap_rows_html,
        weekly_drill_headers=weekly_drilldown_headers,
        weekly_drill_rows=weekly_drilldown_rows,
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
        picker_date=(picker_date_val or now.strftime("%Y-%m-%d")),
        picker_month=(picker_month_val or now.strftime("%Y-%m")),
        dp_total_dispensing=dp_total_dispensing,
        dp_total_drug_fee=dp_total_drug_fee,
        dp_count=dp_count,
        dp_internal_total=dp_internal_total,
        dp_long_count=dp_long_count,
        dp_short_count=dp_short_count,
        t_total_points=t_agg["total_points"],
        t_patient_copay=t_agg["patient_copay"],
        t_drug_fee=t_agg["drug_fee"],
        t_dispensing_fee_total=t_agg["dispensing_fee_total"],
        t_pharmacy_mgmt_fee_total=t_agg["pharmacy_mgmt_fee_total"],
        t_dispensing_base=t_agg["dispensing_base"],
        t_dispensing_add=t_agg["dispensing_add"],
        t_drug_guidance=t_agg["drug_guidance"],
        t_pharmacy_mgmt_other=t_agg["pharmacy_mgmt_other"],
        t_tech_subtotal=(t_agg["dispensing_base"] + t_agg["dispensing_fee_total"] + t_agg["dispensing_add"]),
        t_patient_copay_total=t_agg["patient_copay_total"],
        senteryoyo_row=(
            f'<tr class="copay" style="background:#fee2e2;color:#991b1b"><td>うち選定療養費 (長期収載品) <span class="item-code" style="color:#991b1b">{t_agg["senteryoyo_count"]}件</span> <span class="item-code" style="color:#991b1b">税抜{t_agg["senteryoyo_excl_tax"]:,}+税{t_agg["senteryoyo_tax"]:,}</span></td><td class="num">+{t_agg["senteryoyo_total"]:,} 円</td></tr>'
            if t_agg["senteryoyo_total"] > 0 else ""
        ),
        recent_rows=recent_rows_html,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    _DASHBOARD_CACHE[cache_key] = (_time_mod.time(), html)
    return HTMLResponse(content=html)
