#!/usr/bin/env python3
"""Read-only: auth_status probes per 30s bucket for each agent over the last N minutes."""
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 6
AGENTS = {"1130": "wechat-agent-1130-78c0781a", "xiaohao": "wechat-agent-xiaohao-475cf832"}
buckets = {}
for name, cont in AGENTS.items():
    r = subprocess.run(["docker", "logs", "-t", "--since", f"{minutes}m", cont], capture_output=True, text=True, timeout=120)
    c = Counter()
    for line in (r.stdout + r.stderr).splitlines():
        if "[auth_status]" not in line:
            continue
        t = datetime.fromisoformat(line[:26] + "+00:00").astimezone(CST)
        c[t.replace(second=t.second // 30 * 30, microsecond=0)] += 1
    buckets[name] = c
keys = sorted(set(k for c in buckets.values() for k in c))
print("time(CST)  " + "  ".join(f"{n:>8}" for n in AGENTS) + "   (auth_status probes per 30s)")
for k in keys:
    print(f"{k:%H:%M:%S}   " + "  ".join(f"{buckets[n].get(k, 0):>8}" for n in AGENTS))
