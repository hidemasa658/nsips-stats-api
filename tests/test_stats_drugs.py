import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


HEADERS = {"X-API-Token": "test-token"}


def _insert(source_id: str, drugs: list[dict]) -> None:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prescriptions (source_id, detected_at) VALUES (?, ?)",
        (source_id, "2026-09-18T10:00:00"),
    )
    pid = cur.lastrowid
    for d in drugs:
        conn.execute(
            "INSERT INTO drugs (prescription_id, yj_code, name, quantity, unit) VALUES (?,?,?,?,?)",
            (pid, d["yj_code"], d["name"], d["quantity"], d["unit"]),
        )
    conn.commit()


def test_drugs_stats_empty():
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM prescriptions;")
    client = TestClient(app)
    r = client.get("/stats/drugs", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_drugs_stats_aggregates_by_yj_name_unit():
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM prescriptions;")
    _insert("s1", [{"yj_code": "YJ1", "name": "A", "quantity": 10.0, "unit": "錠"}])
    _insert("s2", [{"yj_code": "YJ1", "name": "A", "quantity": 5.0, "unit": "錠"}])
    _insert("s3", [{"yj_code": "YJ2", "name": "B", "quantity": 3.0, "unit": "g"}])

    client = TestClient(app)
    r = client.get("/stats/drugs", headers=HEADERS)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows[0]["yj_code"] == "YJ1"
    assert rows[0]["n"] == 2
    assert rows[0]["qty"] == 15.0
    assert rows[1]["yj_code"] == "YJ2"
    assert rows[1]["n"] == 1


def test_drugs_stats_requires_token():
    client = TestClient(app)
    r = client.get("/stats/drugs")
    assert r.status_code == 401
