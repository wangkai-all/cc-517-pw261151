"""SQLite persistence for published plans.

Published records are immutable snapshots: forecasts, weights, locks and
the calibrated result, plus a content hash.  There is intentionally no
update or delete operation -- editing a draft must never silently change a
published plan.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "planner.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS published (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


def _connect(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def content_hash(input_obj, result_obj):
    blob = json.dumps({"input": input_obj, "result": result_obj},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def insert_published(db_path, name, input_obj, result_obj):
    digest = content_hash(input_obj, result_obj)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO published (name, created_at, sha256, input_json, result_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, created, digest,
             json.dumps(input_obj, ensure_ascii=False),
             json.dumps(result_obj, ensure_ascii=False)),
        )
        pid = cur.lastrowid
    return {"id": pid, "name": name, "created_at": created, "sha256": digest}


def list_published(db_path):
    if not os.path.exists(db_path):
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, sha256, result_json FROM published"
            " ORDER BY id DESC"
        ).fetchall()
    out = []
    for pid, name, created, digest, result_json in rows:
        try:
            result = json.loads(result_json)
            grand = result.get("grandTotal")
            status = result.get("status")
        except Exception:
            grand, status = None, None
        out.append({"id": pid, "name": name, "created_at": created,
                    "sha256": digest, "grandTotal": grand, "status": status})
    return out


def get_published(db_path, pid):
    if not os.path.exists(db_path):
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, created_at, sha256, input_json, result_json"
            " FROM published WHERE id = ?", (pid,),
        ).fetchone()
    if row is None:
        return None
    pid, name, created, digest, input_json, result_json = row
    return {
        "id": pid, "name": name, "created_at": created, "sha256": digest,
        "input": json.loads(input_json),
        "result": json.loads(result_json),
    }
