import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


HEADERS = {"X-API-Token": "test-token"}


def _clean() -> None:
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM fees; DELETE FROM prescriptions;")


def _insert(source_id: str, clinic_code_enc: str, clinic_name_enc: str) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO prescriptions
           (source_id, detected_at, clinic_code_enc, clinic_name_enc)
           VALUES (?, ?, ?, ?)""",
        (source_id, "2026-09-18T10:00:00", clinic_code_enc, clinic_name_enc),
    )
    conn.commit()


def test_clinics_stats_empty():
    _clean()
    client = TestClient(app)
    r = client.get("/stats/clinics", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_clinics_stats_aggregates_by_encrypted_pair():
    _clean()
    _insert("s1", "ENC_A", "ENC_NAME_A")
    _insert("s2", "ENC_A", "ENC_NAME_A")
    _insert("s3", "ENC_B", "ENC_NAME_B")

    client = TestClient(app)
    r = client.get("/stats/clinics", headers=HEADERS)
    rows = r.json()["rows"]
    assert rows[0]["clinic_code_enc"] == "ENC_A"
    assert rows[0]["n"] == 2
    assert rows[1]["clinic_code_enc"] == "ENC_B"
    assert rows[1]["n"] == 1
