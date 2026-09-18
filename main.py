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
