import sqlite3
from pathlib import Path

from db import connect, init_db


def test_init_db_creates_all_tables(tmp_path):
    p = tmp_path / "stats.db"
    conn = connect(p)
    init_db(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = [r[0] for r in rows]
    assert "prescriptions" in tables
    assert "drugs" in tables
    assert "fees" in tables
    assert "rps" in tables


def test_init_db_creates_indexes(tmp_path):
    p = tmp_path / "stats.db"
    conn = connect(p)
    init_db(conn)
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = [r[0] for r in idx]
    assert "idx_drugs_yj" in names
    assert "idx_fees_mix" in names


def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "stats.db"
    conn = connect(p)
    init_db(conn)
    init_db(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    assert len(rows) == 5  # prescriptions, drugs, fees, rps, fee_master


def test_connect_creates_parent_dir(tmp_path):
    p = tmp_path / "sub" / "stats.db"
    conn = connect(p)
    assert p.parent.exists()
    conn.close()
