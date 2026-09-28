from __future__ import annotations
import json, os, sqlite3
from pathlib import Path

class HistoryDB:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created_at TEXT, seed_url TEXT, status TEXT, result_json TEXT)")
            c.commit()
    def save(self, result):
        with sqlite3.connect(self.path) as c:
            d=result.jsonable() if hasattr(result,"jsonable") else result
            c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)",(d["job_id"],d["started_at"],d["seed_url"],d["status"],json.dumps(d,ensure_ascii=False)))
            c.commit()
    def list(self, limit=30):
        with sqlite3.connect(self.path) as c:
            rows=c.execute("SELECT id,created_at,seed_url,status FROM jobs ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [{"id":r[0],"created_at":r[1],"seed_url":r[2],"status":r[3]} for r in rows]
    def get(self, job_id):
        with sqlite3.connect(self.path) as c:
            row=c.execute("SELECT result_json FROM jobs WHERE id=?",(job_id,)).fetchone()
        return json.loads(row[0]) if row else None
