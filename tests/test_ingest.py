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


def _payload_with_body(source_id: str, body: str, dispense_date: str = "20260925") -> dict:
    p = _payload(source_id)
    p["body_sanitized"] = body
    p["dispense_date"] = dispense_date
    return p


def _body(uketuke: str = "260925001234501", edaban: str = "10", kubun: str = "A") -> str:
    """テスト用最小 body: record 2 だけ含む"""
    return (
        f"VER010603,20260925090000,Medicom,SERVER,14,4,X,ぞうさん薬局,X,X,X,\n"
        f"2,{uketuke},{edaban},{kubun},20260925,,20260925,20260925,0,0,0,1,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X,X\n"
        f"5,100,20,50,0,170,0,70,0,50,0,50,170,510,0,0,0,510,510,510,0,0,0\n"
    )


def test_ingest_dedup_U_overwrites_A():
    """A 受信後に U 受信 → 既存 A を削除して U を残す"""
    client = TestClient(app)
    r1 = client.post(
        "/ingest",
        json=_payload_with_body("au_src_A", _body(kubun="A")),
        headers=_headers(),
    )
    assert r1.json()["status"] == "ok"

    r2 = client.post(
        "/ingest",
        json=_payload_with_body("au_src_U", _body(kubun="U")),
        headers=_headers(),
    )
    assert r2.json()["status"] == "ok"

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, body_sanitized FROM prescriptions "
        "WHERE source_id IN ('au_src_A','au_src_U')"
    ).fetchall()
    assert len(rows) == 1, "A/U 重複が dedup されるべき"
    # U 側が残っているか確認
    assert ",U," in rows[0]["body_sanitized"]


def test_ingest_dedup_A_skipped_when_U_exists():
    """U 既存の状態で 遅れて A 到着 → A は skip されて U だけ残る"""
    client = TestClient(app)
    r1 = client.post(
        "/ingest",
        json=_payload_with_body(
            "au2_U", _body(uketuke="260925009999901", kubun="U")
        ),
        headers=_headers(),
    )
    assert r1.json()["status"] == "ok"

    r2 = client.post(
        "/ingest",
        json=_payload_with_body(
            "au2_A", _body(uketuke="260925009999901", kubun="A")
        ),
        headers=_headers(),
    )
    assert r2.json()["status"] == "superseded"

    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM prescriptions WHERE source_id IN ('au2_A','au2_U')"
    ).fetchone()[0]
    assert n == 1


def test_ingest_different_receipt_no_conflict():
    """同一 dispense_date で 受付番号が異なれば dedup されない"""
    client = TestClient(app)
    r1 = client.post(
        "/ingest",
        json=_payload_with_body(
            "diff_A", _body(uketuke="260925001111101", kubun="A")
        ),
        headers=_headers(),
    )
    r2 = client.post(
        "/ingest",
        json=_payload_with_body(
            "diff_B", _body(uketuke="260925002222201", kubun="U")
        ),
        headers=_headers(),
    )
    assert r1.json()["status"] == "ok"
    assert r2.json()["status"] == "ok"

    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM prescriptions WHERE source_id IN ('diff_A','diff_B')"
    ).fetchone()[0]
    assert n == 2
