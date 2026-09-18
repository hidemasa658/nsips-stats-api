"""nsips-stats-api FastAPI エントリポイント。"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status

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
          (source_id, detected_at, clinic_code_enc, clinic_name_enc,
           prescription_date_enc, doctor_name_enc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload.source_id,
            payload.detected_at,
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
              (prescription_id, fee_type, code_enc, name_enc, count, points, is_mix_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                presc_id,
                f.fee_type,
                f.code_enc,
                f.name_enc,
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
