#!/usr/bin/env python3
"""Read-only: summarize the 1..99 stress test (crash moment, per-step log, send outcomes, EFB recovery)."""
import json
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
DB = "/opt/wechat-hub-deploy/deploy/data/core/core/wechat_core.sqlite"
AGENTS = {"1130": "wechat-agent-1130-78c0781a", "xiaohao": "wechat-agent-xiaohao-475cf832"}
EFBS = {"1130": "wechat-hub-efb-1130", "xiaohao": "wechat-hub-efb"}
since = datetime.now(CST) - timedelta(minutes=50)
since_z = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
out = []


def p(s=""):
    out.append(s)


def logs(container):
    r = subprocess.run(["docker", "logs", "-t", "--since", since_z, container], capture_output=True, text=True, timeout=120)
    return (r.stdout + r.stderr).splitlines()


def cst(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CST)


def ts_of(line):
    t = line.split(" ", 1)[0]
    try:
        return datetime.fromisoformat(t[:26] + "+00:00").astimezone(CST)
    except Exception:
        return None


crashes = {}
for acc, cont in AGENTS.items():
    lines = logs(cont)
    for l in lines:
        if "Segmentation fault" in l or "WeChat process disappeared" in l or "Spawned WeChat" in l:
            t = ts_of(l)
            what = "SEGFAULT" if "Segmentation" in l else ("disappeared" if "disappeared" in l else "respawned")
            p(f"[{acc}] {t:%H:%M:%S.%f}"[:-3] + f" {what}")
            if what == "SEGFAULT":
                crashes.setdefault(acc, []).append(t)
    r = subprocess.run(["docker", "exec", cont, "sh", "-c", "ls -l --time-style=+%H:%M:%S /home/wechat/.xwechat/crashinfo/completed | grep dmp"],
                       capture_output=True, text=True)
    dmps = [x.split()[-2] + " " + x.split()[-1][:8] for x in r.stdout.splitlines()]
    p(f"[{acc}] dumps (UTC time, id): {', '.join(dmps[-4:])}")

p()
for acc, times in crashes.items():
    cont = AGENTS[acc]
    r = subprocess.run(["docker", "exec", cont, "sh", "-c", "cat /tmp/actions.log.1 /tmp/actions.log 2>/dev/null"],
                       capture_output=True, text=True, timeout=60)
    act = r.stdout.splitlines()
    for t in times:
        tu = t.astimezone(timezone.utc)
        lo = (tu - timedelta(seconds=4)).strftime("%H:%M:%S")
        hi = (tu + timedelta(seconds=1)).strftime("%H:%M:%S")
        p(f"== [{acc}] per-step log around crash {t:%H:%M:%S.%f}"[:-3] + f" (UTC {tu:%H:%M:%S.%f})"[:-4])
        window = [l for l in act if lo <= l[:8] <= hi]
        # open (started, not ended) calls at crash time
        started = {}
        crash_hms = tu.strftime("%H:%M:%S.%f")[:12]
        for l in window:
            parts = l.split()
            if len(parts) < 4:
                continue
            tm, pid, ev, tool = parts[0], parts[1], parts[2], parts[3]
            if tm > crash_hms:
                continue
            if ev == "start":
                started[pid] = (tool, tm)
            elif ev == "end":
                started.pop(pid, None)
        p("   in flight at crash: " + (", ".join(f"{tool}(since {tm})" for tool, tm in started.values()) or "none"))
        for l in window[-45:]:
            p("   " + l)

p()
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT account_id, chat_id, kind, status, error, accepted_at, request_json, send_id FROM outbox "
    "WHERE accepted_at >= ? ORDER BY accepted_at", (since_z,)).fetchall()
p(f"== Core outbox since {since:%H:%M} CST: {len(rows)} sends")
by = Counter((r["account_id"], r["status"]) for r in rows)
p("   " + ", ".join(f"{a}/{s}={n}" for (a, s), n in sorted(by.items())))
nums = {}
for r in rows:
    try:
        text = str(json.loads(r["request_json"]).get("text") or "")
    except Exception:
        text = ""
    m = re.fullmatch(r"\s*(\d{1,3})\s*", text)
    key = int(m.group(1)) if m else None
    if key is not None:
        nums.setdefault(key, []).append((r["status"], cst(r["accepted_at"]).strftime("%H:%M:%S"), r["chat_id"][-6:], (r["error"] or "")[:40], r["send_id"][-6:]))
if nums:
    missing = [n for n in range(1, 100) if n not in nums]
    p(f"   numbers seen: {len(nums)}; missing from outbox: {missing[:40]}")
    for n in sorted(nums):
        for st, t, chat, err, sid in nums[n]:
            if st != "sent" or len(nums[n]) > 1:
                p(f"   #{n} {st} {t} chat..{chat} send..{sid} {err}")
chats = Counter(r["chat_id"] for r in rows)
p(f"   chats used: {len(chats)}")

p()
for acc, cont in EFBS.items():
    lines = logs(cont)
    keys = ("Queued rejected", "recovery", "Resend", "resend", "Traceback", "Error", "not sent", "uncertain", "Login notice")
    hits = [l for l in lines if any(k in l for k in keys)]
    p(f"== [{cont}] {len(hits)} notable log lines")
    for l in hits[-25:]:
        t = ts_of(l)
        p(f"   {t:%H:%M:%S} " + l.split(" ", 1)[1][:170] if t else "   " + l[:180])

report = "\n".join(out)
open("/tmp/diag4-report.txt", "w").write(report + "\n")
print(report)
