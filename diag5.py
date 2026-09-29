#!/usr/bin/env python3
"""Read-only follow-up: send steps before the 20:21:59 crash, a11y-dump overlap stats, odd outbox rows."""
import json
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
DB = "/opt/wechat-hub-deploy/deploy/data/core/core/wechat_core.sqlite"
AGENT = "wechat-agent-1130-78c0781a"
CRASH_UTC = "12:21:59.408"
out = []
p = out.append

act = subprocess.run(["docker", "exec", AGENT, "sh", "-c", "cat /tmp/actions.log.1 /tmp/actions.log 2>/dev/null"],
                     capture_output=True, text=True, timeout=60).stdout.splitlines()
p(f"actions.log lines: {len(act)}, first {act[0][:12] if act else '-'} last {act[-1][:12] if act else '-'}")

# 1) send-related steps in the minute before the crash
SEND_TOOLS = {"click", "key", "input", "paste-file", "paste-image", "chat-select", "window-activate", "scroll", "close-media-viewer"}
p("== send-related steps 12:20:50 .. 12:22:05 UTC")
for l in act:
    parts = l.split()
    if len(parts) >= 4 and "12:20:50" <= parts[0][:8] <= "12:22:05" and parts[3] in SEND_TOOLS and parts[2] == "start":
        p("   " + l)

# 2) a11y-dump overlap statistics
def sec(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

open_ = {}
intervals = []
for l in act:
    parts = l.split()
    if len(parts) >= 4 and parts[3] == "a11y-dump":
        if parts[2] == "start":
            open_[parts[1]] = sec(parts[0])
        elif parts[2] == "end" and parts[1] in open_:
            intervals.append((open_.pop(parts[1]), sec(parts[0])))
intervals.sort()
overlaps = 0
max_conc = 1
events = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals])
cur = 0
for _, d in events:
    cur += d
    if d == 1 and cur >= 2:
        overlaps += 1
    max_conc = max(max_conc, cur)
dur = [b - a for a, b in intervals]
span = (intervals[-1][1] - intervals[0][0]) if intervals else 0
p(f"== a11y-dump: {len(intervals)} calls over {span/60:.1f} min = {len(intervals)/max(span,1):.2f}/s, "
  f"avg {sum(dur)/max(len(dur),1)*1000:.0f} ms, max {max(dur or [0])*1000:.0f} ms")
p(f"   starts that overlapped another dump: {overlaps} ({overlaps/max(len(intervals),1)*100:.1f}%), max concurrent {max_conc}")
slow = sorted(((b - a, a) for a, b in intervals), reverse=True)[:5]
p("   slowest: " + ", ".join(f"{d*1000:.0f}ms@{int(a//3600):02d}:{int(a%3600//60):02d}:{a%60:06.3f}" for d, a in slow))
c = sec(CRASH_UTC)
near = [(a, b) for a, b in intervals if a <= c + 0.1 and b >= c - 1.0]
p("   dumps touching crash window: " + ", ".join(f"{a%60:.3f}-{b%60:.3f}" for a, b in near))

# 3) odd outbox rows
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
con.row_factory = sqlite3.Row
since = (datetime.now(timezone.utc) - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
p("== outbox rows not 'sent', or text 82/83, last 60 min")
for r in con.execute("SELECT * FROM outbox WHERE accepted_at >= ? ORDER BY accepted_at", (since,)):
    try:
        req = json.loads(r["request_json"])
    except Exception:
        req = {}
    text = str(req.get("text") or "")
    if r["status"] != "sent" or text.strip() in {"81", "82", "83", "84"}:
        t = datetime.fromisoformat(r["accepted_at"].replace("Z", "+00:00")).astimezone(CST)
        u = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00")).astimezone(CST)
        p(f"   {t:%H:%M:%S}->{u:%H:%M:%S} {r['status']:<8} text={text[:20]!r} chat..{r['chat_id'][-8:]} "
          f"req..{str(r['client_request_id'])[-8:]} key..{str(r['idempotency_key'] or '')[-8:]} tries={r['attempt_count']} err={(r['error'] or '')[:60]}")

# 4) EFB-1130 log lines about sends in the crash window
logs = subprocess.run(["docker", "logs", "-t", "--since", "2026-09-29T12:15:00Z", "wechat-hub-efb-1130"],
                      capture_output=True, text=True, timeout=60)
lines = (logs.stdout + logs.stderr).splitlines()
p(f"== EFB-1130 log since 20:15 CST: {len(lines)} lines; levels " + str(Counter(
    w for l in lines for w in ("INFO", "WARNING", "ERROR") if f"[{w}]" in l or f" {w} " in l)))
for l in lines:
    if any(k in l for k in ("rejected", "Queued", "certainty", "not sent", "Traceback", "EFBMessageError", "排队", "补发")):
        p("   " + l[11:23] + " " + l.split(" ", 1)[1][:160])

report = "\n".join(out)
open("/tmp/diag5-report.txt", "w").write(report + "\n")
print(report)
