#!/usr/bin/env python3
"""Read-only: 1130 login-state timeline today (agent auth_status + Core account events)."""
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
DB = "/opt/wechat-hub-deploy/deploy/data/core/core/wechat_core.sqlite"
since = datetime.now(CST).replace(hour=12, minute=0, second=0, microsecond=0)
since_z = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cst(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")[:32]).astimezone(CST).strftime("%H:%M:%S")


print("== agent auth_status transitions (1130)")
p = subprocess.run(["docker", "logs", "-t", "--since", since_z, "wechat-agent-1130-78c0781a"],
                   capture_output=True, text=True, timeout=120)
prev = None
pat = re.compile(r"\[auth_status\] view=(\w+), status=(\w+)")
for line in (p.stdout + p.stderr).splitlines():
    m = pat.search(line)
    if not m:
        continue
    cur = (m.group(1), m.group(2))
    if cur != prev:
        print(f"  {cst(line.split(' ', 1)[0])} view={cur[0]} status={cur[1]}")
        prev = cur

print("== Core account events (1130)")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
prev = None
for et, occ, payload in con.execute(
    "SELECT event_type, occurred_at, payload_json FROM events "
    "WHERE account_id LIKE '%1130%' AND event_type NOT LIKE 'message%' AND occurred_at >= ? ORDER BY cursor",
    (since_z,),
):
    try:
        d = json.loads(payload)
    except Exception:
        d = {}
    acc = d.get("account") if isinstance(d.get("account"), dict) else d
    state = acc.get("state", "")
    login = (acc.get("runtime") or {}).get("wechat_login_status", "")
    cur = (et, state, login)
    if cur != prev:
        print(f"  {cst(occ)} {et} state={state} login={login}")
        prev = cur
print("== current account row")
for r in con.execute("SELECT account_id, state, updated_at FROM accounts"):
    print("  ", r[0], r[1], cst(r[2]))
