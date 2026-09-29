#!/usr/bin/env python3
"""Read-only: symbolize the 1130 WeChat crash dumps against the wechat binary's symbol tables.

Copies the binary and the minidumps out of the container into /tmp/symwork, then prints,
for each dump, the crashing function and a heuristic call chain (return addresses found on
the crashing thread's stack that point into wechat's code).
"""
import bisect
import os
import shutil
import struct
import subprocess
import sys

CONTAINER = "wechat-agent-1130-78c0781a"
DUMPS = "/home/wechat/.xwechat/crashinfo/completed"
WORK = "/tmp/symwork"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def wechat_exe():
    r = sh(["docker", "exec", CONTAINER, "sh", "-c",
            "for p in $(pgrep -f wechat); do readlink -f /proc/$p/exe; done | sort | uniq -c | sort -rn"])
    for line in r.stdout.splitlines():
        path = line.split()[-1]
        if os.path.basename(path).lower().startswith("wechat") and not path.endswith(("bash", "sh", "python3")):
            return path
    sys.exit("!! wechat binary not found:\n" + r.stdout + r.stderr)


# ---------- ELF symbols ----------
def elf_symbols(path):
    f = open(path, "rb")
    data = f.read()
    assert data[:4] == b"\x7fELF" and data[4] == 2, "64-bit ELF expected"
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, off)
        secs.append(dict(name=name, type=typ, addr=addr, offset=offset, size=size, link=link, entsize=entsize))
    shstr = secs[e_shstrndx]

    def secname(s):
        start = shstr["offset"] + s["name"]
        return data[start:data.index(b"\0", start)].decode(errors="replace")

    names = {secname(s): s for s in secs}
    syms = []
    counts = {}
    for tabname in (".symtab", ".dynsym"):
        tab = names.get(tabname)
        if not tab:
            continue
        strtab = secs[tab["link"]]
        n = 0
        for off in range(tab["offset"], tab["offset"] + tab["size"], 24):
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", data, off)
            if st_value == 0 or (st_info & 0xF) != 2:  # functions only
                continue
            start = strtab["offset"] + st_name
            nm = data[start:data.index(b"\0", start)].decode(errors="replace")
            syms.append((st_value, st_size, nm))
            n += 1
        counts[tabname] = n
    syms.sort()
    text = names.get(".text")
    return syms, counts, (text["addr"], text["addr"] + text["size"]) if text else None


def demangle(names):
    names = list(names)
    if not names or not shutil.which("c++filt"):
        return {n: n for n in names}
    r = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True, text=True)
    out = r.stdout.splitlines()
    return dict(zip(names, out)) if len(out) == len(names) else {n: n for n in names}


# ---------- minidump ----------
def read_dump(path):
    d = open(path, "rb").read()
    nstreams, dir_rva = struct.unpack_from("<II", d, 8)
    streams = {}
    for i in range(nstreams):
        t, size, rva = struct.unpack_from("<III", d, dir_rva + 12 * i)
        streams[t] = (size, rva)
    _, erva = streams[6]
    tid = struct.unpack_from("<I", d, erva)[0]
    code, flags = struct.unpack_from("<II", d, erva + 8)
    fault = struct.unpack_from("<Q", d, erva + 24)[0]
    csize, crva = struct.unpack_from("<II", d, erva + 160)
    rsp = struct.unpack_from("<Q", d, crva + 152)[0]
    rip = struct.unpack_from("<Q", d, crva + 248)[0]
    _, mrva = streams[4]
    base, size = struct.unpack_from("<QI", d, mrva + 4)
    modules = []
    nmod = struct.unpack_from("<I", d, mrva)[0]
    for i in range(nmod):
        off = mrva + 4 + 108 * i
        mbase, msize = struct.unpack_from("<QI", d, off)
        name_rva = struct.unpack_from("<I", d, off + 20)[0]
        nlen = struct.unpack_from("<I", d, name_rva)[0]
        mname = d[name_rva + 4:name_rva + 4 + nlen].decode("utf-16-le", errors="replace")
        modules.append((mbase, mbase + msize, mname))
    modules.sort()
    # thread list: find crashing thread's stack memory
    stack = b""
    stack_start = 0
    _, trva = streams[3]
    nthreads = struct.unpack_from("<I", d, trva)[0]
    for i in range(nthreads):
        off = trva + 4 + 48 * i
        t_id = struct.unpack_from("<I", d, off)[0]
        if t_id == tid:
            stack_start, ssize, srva = struct.unpack_from("<QII", d, off + 24)
            stack = d[srva:srva + ssize]
            break
    return dict(tid=tid, code=code, flags=flags, fault=fault, rip=rip, rsp=rsp, base=base,
                size=size, stack=stack, stack_start=stack_start, modules=modules)


def main():
    os.makedirs(WORK, exist_ok=True)
    exe = wechat_exe()
    print("wechat binary:", exe)
    local = os.path.join(WORK, "wechat.bin")
    if not os.path.exists(local):
        subprocess.run(["docker", "cp", f"{CONTAINER}:{exe}", local], check=True)
    dumps = os.path.join(WORK, "dumps")
    shutil.rmtree(dumps, ignore_errors=True)
    subprocess.run(["docker", "cp", f"{CONTAINER}:{DUMPS}", dumps], check=True)
    syms, counts, text = elf_symbols(local)
    print("function symbols:", counts, "text:", tuple(hex(x) for x in text) if text else None)
    addrs = [s[0] for s in syms]

    def lookup(off):
        i = bisect.bisect_right(addrs, off) - 1
        if i < 0:
            return None
        a, sz, nm = syms[i]
        inside = sz == 0 or off < a + sz
        return nm, off - a, inside

    libcache = {}

    def lib_syms(path):
        if path not in libcache:
            local_lib = os.path.join(WORK, "libs", path.strip("/").replace("/", "_"))
            os.makedirs(os.path.dirname(local_lib), exist_ok=True)
            if not os.path.exists(local_lib):
                subprocess.run(["docker", "cp", "-L", f"{CONTAINER}:{path}", local_lib], capture_output=True)
            try:
                ls, _, lt = elf_symbols(local_lib)
                libcache[path] = (ls, [x[0] for x in ls], lt)
            except Exception:
                libcache[path] = ([], [], None)
        return libcache[path]

    def resolve(addr, modules):
        for mb, me, mn in modules:
            if mb <= addr < me:
                if mn == exe or os.path.basename(mn) == os.path.basename(exe):
                    o = addr - mb
                    if not (text and text[0] <= o < text[1]):
                        return None
                    return (os.path.basename(mn), o, None)
                ls, la, lt = lib_syms(mn)
                o = addr - mb
                if lt and not (lt[0] <= o < lt[1]):
                    return None
                i = bisect.bisect_right(la, o) - 1
                if i < 0:
                    return (os.path.basename(mn), o, None)
                a, sz, nm = ls[i]
                return (os.path.basename(mn), o, (nm, o - a, sz == 0 or o < a + sz))
        return None

    rows = []
    names = set()
    for fn in sorted(os.listdir(dumps)):
        if not fn.endswith(".dmp"):
            continue
        info = read_dump(os.path.join(dumps, fn))
        st = info["stack"]
        rsp_index = max(0, info["rsp"] - info["stack_start"])
        chain = []
        for pos in range(rsp_index - rsp_index % 8, min(len(st), rsp_index + 32768), 8):
            v = struct.unpack_from("<Q", st, pos)[0]
            r = resolve(v, info["modules"])
            if r:
                chain.append(r)
                if r[2]:
                    names.add(r[2][0])
            if len(chain) >= 60:
                break
        rows.append((fn, info, chain))
    dem = demangle(names)
    out = []
    for fn, info, chain in rows:
        out.append(f"\n== {fn[:8]} signal={info['code']} fault=0x{info['fault']:x} rip=wechat+0x{info['rip'] - info['base']:x}")
        last = None
        for mod, o, sym in chain:
            if sym:
                nm, delta, inside = sym
                line = f"   <- {mod[:22]} {dem.get(nm, nm)[:100]} +0x{delta:x}{'' if inside else ' ?'}"
            else:
                line = f"   <- {mod[:22]} +0x{o:x}"
            if line != last:
                out.append(line)
            last = line
    report = "\n".join(out)
    open(os.path.join(WORK, "report.txt"), "w").write(report + "\n")
    libs_seen = sorted({m for _, _, ch in rows for m, _, s2 in ch if not m.lower().startswith("wechat")})
    print("modules on crashing stacks (besides wechat):", ", ".join(libs_seen))
    print("full report: /tmp/symwork/report.txt")
    # print the most informative dump (latest) with non-wechat frames only + first wechat frames
    fn, info, chain = rows[-1]
    print(f"== {fn[:8]} rip=wechat+0x{info['rip'] - info['base']:x}")
    for mod, o, sym in chain:
        if sym:
            nm, delta, inside = sym
            print(f"   <- {mod[:22]} {dem.get(nm, nm)[:110]} +0x{delta:x}")


if __name__ == "__main__":
    main()
