import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


HEADERS = {"X-API-Token": "test-token"}


def _clean() -> None:
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM fees; DELETE FROM prescriptions;")


def test_export_returns_all_prescriptions():
    _clean()
    conn = get_conn()
    conn.execute(
        """INSERT INTO prescriptions
           (source_id, detected_at, clinic_code_enc, clinic_name_enc)
           VALUES (?, ?, ?, ?)""",
        ("s1", "2026-09-18T10:00:00", "ENC_C", "ENC_N"),
    )
    conn.commit()
    client = TestClient(app)
    r = client.get("/stats/export/prescriptions", headers=HEADERS)
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["source_id"] == "s1"
    assert body["rows"][0]["clinic_code_enc"] == "ENC_C"
