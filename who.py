#!/usr/bin/env python3
"""Read-only: which containers open TCP connections to Core (:8080) and to the agents, sampled for ~10s."""
import json
import socket
import struct
import subprocess
import time
from collections import Counter

def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout

ids = sh("docker", "ps", "-q").split()
info = json.loads(sh("docker", "inspect", *ids))
ip2name, pid_of = {}, {}
for c in info:
    name = c["Name"].lstrip("/")
    pid_of[name] = c["State"]["Pid"]
    for net in (c["NetworkSettings"].get("Networks") or {}).values():
        if net.get("IPAddress"):
            ip2name[net["IPAddress"]] = name
    if c["HostConfig"].get("NetworkMode", "").startswith("container:"):
        ip2name.setdefault("shared-ns:" + name, c["HostConfig"]["NetworkMode"])
print("network modes:", {c["Name"].lstrip("/"): c["HostConfig"].get("NetworkMode") for c in info})

def hexip(h):
    return socket.inet_ntoa(struct.pack("<I", int(h, 16)))

def conns(pid, port):
    seen = set()
    for f in ("tcp", "tcp6"):
        try:
            lines = open(f"/proc/{pid}/net/{f}").read().splitlines()[1:]
        except OSError:
            continue
        for l in lines:
            p = l.split()
            lip, lport = p[1].split(":")
            rip, rport = p[2].split(":")
            if int(lport, 16) == port and len(rip) == 8 and int(rip, 16) != 0:
                seen.add((hexip(rip), int(rport, 16), p[3]))
    return seen

targets = {"wechat-hub-core": 8080}
for name in pid_of:
    if name.startswith("wechat-agent-"):
        targets[name] = 6174
counts = {t: Counter() for t in targets}
uniq = {t: set() for t in targets}
for _ in range(40):
    for t, port in targets.items():
        for rip, rport, st in conns(pid_of[t], port):
            key = (rip, rport)
            if key not in uniq[t]:
                uniq[t].add(key)
                counts[t][ip2name.get(rip, rip)] += 1
    time.sleep(0.25)
for t in targets:
    print(f"== new connections into {t}:{targets[t]} in ~10s:", dict(counts[t]) or "none seen")
