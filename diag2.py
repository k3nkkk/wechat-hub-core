#!/usr/bin/env python3
"""Read-only: Core outbox sends around each 1130 WeChat crash."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
DB = "/opt/wechat-hub-deploy/deploy/data/core/core/wechat_core.sqlite"
CRASHES = ["2026-09-28 19:54:39", "2026-09-28 23:14:47", "2026-09-29 16:49:15", "2026-09-29 17:45:08"]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
con.row_factory = sqlite3.Row


def z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cst(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CST).strftime("%H:%M:%S")


lines = []
for c in CRASHES:
    t = datetime.strptime(c, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    lines.append(f"== crash {c} CST")
    rows = con.execute(
        "SELECT kind, status, accepted_at, updated_at, request_json, error FROM outbox "
        "WHERE account_id LIKE '%1130%' AND accepted_at BETWEEN ? AND ? ORDER BY accepted_at",
        (z(t - timedelta(minutes=20)), z(t + timedelta(minutes=2))),
    ).fetchall()
    if not rows:
        lines.append("  (no sends in window)")
    for r in rows:
        try:
            req = json.loads(r["request_json"] or "{}")
        except Exception:
            req = {}
        text = str(req.get("text") or req.get("message") or "")
        media = req.get("image_path") or req.get("file_path") or req.get("media_path") or ""
        extra = f"len={len(text)} nl={text.count(chr(10))}" if text else ""
        if media:
            extra += f" media={str(media).rsplit('/', 1)[-1][:24]}"
        keys = ",".join(sorted(k for k in req.keys() if k not in ("text", "message")))[:60]
        gap = (t - datetime.fromisoformat(r["accepted_at"].replace("Z", "+00:00"))).total_seconds()
        lines.append(
            f"  {cst(r['accepted_at'])} (-{int(gap)}s) {r['kind']:<6} {r['status']:<9} {extra} keys={keys}"
        )
report = "\n".join(lines)
open("/tmp/diag2-report.txt", "w").write(report + "\n")
print(report)
