import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


HEADERS = {"X-API-Token": "test-token"}


def _clean() -> None:
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM fees; DELETE FROM prescriptions;")


def _add_prescription(source_id: str, drug_count: int, is_mix: bool) -> None:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prescriptions (source_id, detected_at) VALUES (?, ?)",
        (source_id, "2026-09-18T10:00:00"),
    )
    pid = cur.lastrowid
    for i in range(drug_count):
        conn.execute(
            "INSERT INTO drugs (prescription_id, yj_code) VALUES (?, ?)",
            (pid, f"YJ_{i}"),
        )
    if is_mix:
        conn.execute(
            "INSERT INTO fees (prescription_id, fee_type, is_mix_flag) VALUES (?, ?, 1)",
            (pid, "7"),
        )
    conn.commit()


def test_mix_empty():
    _clean()
    client = TestClient(app)
    r = client.get("/stats/mix", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"total": 0, "breakdown": []}


def test_mix_counts_and_breakdown():
    _clean()
    _add_prescription("a", drug_count=2, is_mix=True)
    _add_prescription("b", drug_count=2, is_mix=True)
    _add_prescription("c", drug_count=3, is_mix=True)
    _add_prescription("d", drug_count=1, is_mix=False)

    client = TestClient(app)
    r = client.get("/stats/mix", headers=HEADERS)
    body = r.json()
    assert body["total"] == 3
    assert body["breakdown"] == [
        {"drug_count": 2, "n": 2},
        {"drug_count": 3, "n": 1},
    ]
