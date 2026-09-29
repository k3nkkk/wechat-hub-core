#!/usr/bin/env python3
"""EFB send-recovery rollout helper (runs on the VM host as root).

  python3 efbpatch.py check    # copy running EFB code out, dry-run the patch (no changes)
  python3 efbpatch.py build    # build wechat-hub-efb-linux-wechat-slave:test-ordered9 from the running image + patch
  python3 efbpatch.py deploy   # point EFB_IMAGE at test-ordered9 and recreate both EFB services
  python3 efbpatch.py rollback # point EFB_IMAGE back at the previous image and recreate both EFB services
"""
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/k3nkkk/wechat-hub-core/COMMIT/"
COMMIT = os.environ.get("EFBPATCH_COMMIT", "c38c04e5f2de2d117c04068cf70e947661b950a7")
CONTAINER = "wechat-hub-efb-1130"
SRC = "/opt/efb-linux-wechat-slave"
WORK = "/opt/build/efb-recovery"
DEPLOY = "/opt/wechat-hub-deploy/deploy"
NEW_TAG = "wechat-hub-efb-linux-wechat-slave:test-ordered9"
PATCH_SHA256 = "c49c18090f63fe6cd9fd48c5a2f6d658eb2c3223c134f1ef05dd2327db843160"


def run(cmd, **kw):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=kw.pop("check", True), text=True, **kw)


def current_image():
    out = subprocess.run(["docker", "inspect", "--format", "{{.Config.Image}}", CONTAINER],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def fetch_patch():
    path = os.path.join(WORK, "recovery.patch")
    url = BASE.replace("COMMIT", COMMIT) + "recovery.patch"
    data = urllib.request.urlopen(url, timeout=60).read()
    with open(path, "wb") as f:
        f.write(data)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    print("patch sha256", digest)
    if digest != PATCH_SHA256:
        sys.exit("!! patch checksum mismatch, refusing to continue")
    return path


def check():
    os.makedirs(WORK, exist_ok=True)
    patch = fetch_patch()
    src = os.path.join(WORK, "src")
    shutil.rmtree(src, ignore_errors=True)
    run(["docker", "cp", f"{CONTAINER}:{SRC}", src])
    print("running image:", current_image())
    for name in ("ComWechat.py", "Core.py"):
        p = os.path.join(src, "efb_wechat_comwechat_slave", name)
        print(name, hashlib.md5(open(p, "rb").read()).hexdigest())
    res = run(["patch", "--dry-run", "-p1", "-d", src, "-i", patch], check=False,
              capture_output=True)
    print(res.stdout[-3000:], res.stderr[-2000:])
    print("DRY-RUN", "OK" if res.returncode == 0 else f"FAILED rc={res.returncode}")


def build():
    patch = fetch_patch()
    base = current_image()
    with open(os.path.join(WORK, "Dockerfile.recovery"), "w") as f:
        f.write(
            f"FROM {base}\n"
            "COPY recovery.patch /tmp/recovery.patch\n"
            f"RUN cd {SRC} && patch -p1 -i /tmp/recovery.patch && python3 -m py_compile "
            "efb_wechat_comwechat_slave/ComWechat.py efb_wechat_comwechat_slave/SendRecovery.py "
            "&& rm -f /tmp/recovery.patch\n"
        )
    with open(os.path.join(WORK, "base-image.txt"), "w") as f:
        f.write(base + "\n")
    run(["docker", "build", "--pull=false", "-f", os.path.join(WORK, "Dockerfile.recovery"), "-t", NEW_TAG, WORK])
    print("BUILD OK", NEW_TAG, "from", base)


def set_env_image(image):
    env = os.path.join(DEPLOY, ".env")
    lines = open(env).read().splitlines()
    out, found = [], False
    for line in lines:
        if line.startswith("EFB_IMAGE="):
            print("old:", line)
            line = f"EFB_IMAGE={image}"
            found = True
        out.append(line)
    if not found:
        sys.exit("!! EFB_IMAGE not found in .env")
    shutil.copy(env, env + ".bak-recovery")
    open(env, "w").write("\n".join(out) + "\n")
    print("new:", f"EFB_IMAGE={image}")


def recreate():
    run(["docker", "compose", "up", "-d", "--no-deps", "wechat-efb", "wechat-efb-1130"], cwd=DEPLOY)
    run(["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Status}}"])


def deploy():
    set_env_image(NEW_TAG)
    recreate()


def rollback():
    base = open(os.path.join(WORK, "base-image.txt")).read().strip()
    set_env_image(base)
    recreate()


if __name__ == "__main__":
    {"check": check, "build": build, "deploy": deploy, "rollback": rollback}[sys.argv[1] if len(sys.argv) > 1 else "check"]()
