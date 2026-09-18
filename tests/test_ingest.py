import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


def _headers() -> dict:
    return {"X-API-Token": "test-token"}


def _payload(source_id: str = "src1") -> dict:
    return {
        "source_id": source_id,
        "detected_at": "2026-09-18T10:00:00",
        "clinic_code_enc": "ENC_CLINIC_CODE",
        "clinic_name_enc": "ENC_CLINIC_NAME",
        "prescription_date_enc": "ENC_DATE",
        "doctor_name_enc": "ENC_DOC",
        "drugs": [
            {
                "rp_no_enc": "ENC_RP",
                "yj_code": "6152004F2089",
                "name": "ビブラマイシン錠100mg",
                "quantity": 22.0,
                "unit": "錠",
            }
        ],
        "fees": [
            {
                "fee_type": "7",
                "code_enc": "ENC_CODE",
                "name_enc": "ENC_NAME_MIX",
                "count": 1,
                "points": 100,
                "is_mix_flag": True,
            }
        ],
    }


def test_ingest_inserts_prescription_and_children():
    client = TestClient(app)
    r = client.post("/ingest", json=_payload(), headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["prescription_id"], int)

    conn = get_conn()
    presc = conn.execute("SELECT * FROM prescriptions WHERE source_id='src1'").fetchone()
    assert presc is not None
    drugs = conn.execute("SELECT * FROM drugs WHERE prescription_id=?", (presc["id"],)).fetchall()
    assert len(drugs) == 1
    assert drugs[0]["yj_code"] == "6152004F2089"
    fees = conn.execute("SELECT * FROM fees WHERE prescription_id=?", (presc["id"],)).fetchall()
    assert len(fees) == 1
    assert fees[0]["is_mix_flag"] == 1


def test_ingest_duplicate_source_id_is_skipped():
    client = TestClient(app)
    r1 = client.post("/ingest", json=_payload("dup"), headers=_headers())
    assert r1.status_code == 200
    r2 = client.post("/ingest", json=_payload("dup"), headers=_headers())
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"

    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM prescriptions WHERE source_id='dup'").fetchone()[0]
    assert n == 1


def test_ingest_requires_token():
    client = TestClient(app)
    r = client.post("/ingest", json=_payload("noauth"))
    assert r.status_code == 401
