#!/usr/bin/env python3
"""Read-only reconciliation: WeChat (Core) <-> Telegram (EFB) since a given time.

Usage: python3 diag.py [HH]   (local CST hour today, default 12)
Writes the full report to /tmp/diag-report.txt and prints a summary.
"""
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
hour = int(sys.argv[1]) if len(sys.argv) > 1 else 12
now = datetime.now(CST)
since_local = now.replace(hour=hour, minute=0, second=0, microsecond=0)
since_utc = since_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

ROOTS = os.environ.get("DIAG_ROOTS", "/opt/wechat-hub-deploy:/var/lib/docker/volumes").split(":")
WANT = {"wechat_core.sqlite", "core-effect-ledger.sqlite3", "core-message-mapping.sqlite3"}
SKIP_DIRS = {"xwechat_files", "msg", "Img", "attach", "cache", "Cache", "node_modules", ".git"}

found = defaultdict(list)
for root in ROOTS:
    base_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count("/") - base_depth > 9:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f in WANT:
                found[f].append(os.path.join(dirpath, f))

out_lines = []


def out(s=""):
    out_lines.append(s)


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def cst(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CST).strftime("%H:%M:%S")
    except Exception:
        return ts[:19]


out(f"== since {since_local:%m-%d %H:%M} CST ({since_utc}), now {now:%H:%M}")
for k in sorted(found):
    for p in found[k]:
        out(f"file {k}: {p}")

core_paths = found.get("wechat_core.sqlite", [])
if not core_paths:
    out("!! Core DB not found")
    print("\n".join(out_lines))
    sys.exit(1)
core_path = max(core_paths, key=lambda p: os.path.getmtime(p))
core = ro(core_path)
core.row_factory = sqlite3.Row

chat_names = {}
for r in core.execute("SELECT account_id, chat_id, display_name FROM chats"):
    chat_names[(r["account_id"], r["chat_id"])] = r["display_name"]

msgs = core.execute(
    "SELECT account_id, message_id, chat_id, type, direction, created_at, substr(text,1,24) AS t "
    "FROM messages WHERE created_at >= ? ORDER BY created_at",
    (since_utc,),
).fetchall()

outbox = core.execute(
    "SELECT send_id, kind, account_id, chat_id, status, error, accepted_at, updated_at, echo_message_id "
    "FROM outbox WHERE accepted_at >= ? ORDER BY accepted_at",
    (since_utc,),
).fetchall()
echo_ids = {(r["account_id"], r["echo_message_id"]) for r in outbox if r["echo_message_id"]}

# All outbox echo ids (older sends can still echo into the window)
for r in core.execute("SELECT account_id, echo_message_id FROM outbox WHERE echo_message_id != ''"):
    echo_ids.add((r[0], r[1]))

ledger = {}
ledger_consumer = {}
for p in found.get("core-effect-ledger.sqlite3", []):
    try:
        c = ro(p)
        for acc, mid, status, consumer in c.execute(
            "SELECT account_id, message_id, status, consumer_id FROM effect_ledger WHERE updated_at >= ? OR created_at >= ?",
            (since_utc, since_utc),
        ):
            ledger[(acc, mid)] = status
            ledger_consumer.setdefault(acc, set()).add(f"{consumer} @ {p.split('/profiles/')[-1][:40]}")
        c.close()
    except Exception as e:
        out(f"!! ledger {p}: {e}")

mapped = set()
for p in found.get("core-message-mapping.sqlite3", []):
    try:
        c = ro(p)
        for acc, mid, tg in c.execute(
            "SELECT account_id, core_message_id, telegram_message_id FROM message_mapping WHERE created_at >= ?",
            (since_utc,),
        ):
            if mid:
                mapped.add((acc, mid))
        c.close()
    except Exception as e:
        out(f"!! mapping {p}: {e}")

out()
out("== WeChat -> Telegram (Core messages vs EFB ledger)")
by_acc = defaultdict(list)
for m in msgs:
    by_acc[m["account_id"]].append(m)

problems = []
for acc, rows in sorted(by_acc.items()):
    stat = Counter()
    for m in rows:
        key = (acc, m["message_id"])
        if m["direction"] == "outgoing" and key in echo_ids:
            stat["tg_echo(skip)"] += 1
            continue
        s = ledger.get(key)
        if s is None:
            s = "MAPPED_ONLY" if key in mapped else "MISSING"
        stat[s] += 1
        if s not in ("DELIVERED",):
            problems.append((m, s))
    consumers = "; ".join(sorted(ledger_consumer.get(acc, {"<no ledger rows>"})))
    out(f"[{acc}] core={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(stat.items())))
    out(f"    ledger: {consumers}")

out()
out(f"== not delivered ({len(problems)}), time CST | acct | chat | type/dir | status | text")
for m, s in problems:
    name = chat_names.get((m["account_id"], m["chat_id"]), m["chat_id"])[:10]
    text = (m["t"] or "").replace("\n", " ")
    out(f"{cst(m['created_at'])} {m['account_id'][:8]} {name} {m['type']}/{m['direction'][:3]} {s} {text}")

out()
out("== Telegram -> WeChat (Core outbox)")
ostat = Counter((r["account_id"], r["status"]) for r in outbox)
for (acc, st), n in sorted(ostat.items()):
    out(f"[{acc}] {st}={n}")
for r in outbox:
    if r["status"] != "sent":
        name = chat_names.get((r["account_id"], r["chat_id"]), r["chat_id"])[:10]
        out(f"  {cst(r['accepted_at'])} {r['account_id'][:8]} {name} {r['kind']} {r['status']} {(r['error'] or '')[:50]}")

out()
out("== EFB refusals in container logs (not sent to WeChat)")
for cont in ("wechat-hub-efb", "wechat-hub-efb-1130"):
    try:
        logs = subprocess.run(
            ["docker", "logs", "-t", "--since", since_utc, cont],
            capture_output=True, text=True, timeout=60,
        )
        lines = (logs.stdout + logs.stderr).splitlines()
        hits = [l for l in lines if "Message is not sent" in l]
        reasons = Counter()
        for l in lines:
            if "Core rejected message" in l and "EFBMessageError" in l:
                reasons[l.split("Core rejected message", 1)[1][:30]] += 1
        out(f"[{cont}] not_sent={len(hits)} " + " ".join(f"{k.strip()}x{v}" for k, v in reasons.items()))
        for l in hits[:15]:
            ts = l.split(" ", 1)[0]
            out(f"  {cst(ts)}")
    except Exception as e:
        out(f"!! {cont}: {e}")

report = "\n".join(out_lines)
with open("/tmp/diag-report.txt", "w") as f:
    f.write(report + "\n")
print(report)
