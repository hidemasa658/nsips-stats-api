import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app, get_conn  # noqa: E402


def _clean() -> None:
    conn = get_conn()
    conn.executescript("DELETE FROM drugs; DELETE FROM fees; DELETE FROM prescriptions;")


def test_dashboard_requires_token():
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 401


def test_dashboard_accepts_header_token():
    _clean()
    client = TestClient(app)
    r = client.get("/dashboard", headers={"X-API-Token": "test-token"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_dashboard_accepts_query_token():
    _clean()
    client = TestClient(app)
    r = client.get("/dashboard?token=test-token")
    assert r.status_code == 200
    assert "nsips-stats" in r.text


def test_dashboard_shows_drug_data():
    _clean()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prescriptions (source_id, detected_at) VALUES (?, ?)",
        ("s1", "2026-09-18T10:00:00"),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO drugs (prescription_id, yj_code, name, quantity, total_quantity, unit) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, "YJ001", "テスト錠", 10.5, 10.5, "錠"),
    )
    conn.commit()

    client = TestClient(app)
    r = client.get("/dashboard?token=test-token")
    assert "YJ001" in r.text
    assert "テスト錠" in r.text
    assert "10.50" in r.text


def test_dashboard_escapes_html():
    _clean()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prescriptions (source_id, detected_at) VALUES (?, ?)",
        ("xss", "2026-09-18T10:00:00"),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO drugs (prescription_id, yj_code, name, quantity, unit) VALUES (?, ?, ?, ?, ?)",
        (pid, "YJ<script>", "<img onerror=x>", 1.0, "錠"),
    )
    conn.commit()

    client = TestClient(app)
    r = client.get("/dashboard?token=test-token")
    # ユーザ入力 (YJ<script>) が生の状態で埋め込まれていないことを確認
    assert "YJ<script>" not in r.text
    assert "YJ&lt;script&gt;" in r.text
    assert "<img onerror=x>" not in r.text


def test_dashboard_wrong_token():
    client = TestClient(app)
    r = client.get("/dashboard?token=wrong")
    assert r.status_code == 401
