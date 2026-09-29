#!/bin/bash
# Install/uninstall per-step timing logs for the agent's tool scripts.
# Run INSIDE an agent-wechat container:  bash actlog-setup.sh install|uninstall|status
# Log: /tmp/actions.log  (time pid start|end tool [rc])  -- message text is never logged.
set -euo pipefail
T=${ACTLOG_TOOLS:-/opt/tools}
LOG=/tmp/actions.log
BASH_TOOLS="click key input paste-file paste-image window-activate chat-select scroll screenshot close-media-viewer"
PY_TOOLS="a11y-dump"

install_helper() {
  cat > "$T/_actlog.sh" <<'EOF'
# sourced by tool scripts; logs start/end with ms timestamps (no arguments are logged)
_AL=/tmp/actions.log
if [ -f "$_AL" ] && [ "$(stat -c %s "$_AL" 2>/dev/null || echo 0)" -gt 20000000 ]; then
  mv -f "$_AL" "$_AL.1" 2>/dev/null || true
fi
_al_name=$(basename "$0")
echo "$(date +%H:%M:%S.%3N) $$ start $_al_name" >> "$_AL"
trap 'echo "$(date +%H:%M:%S.%3N) $$ end $_al_name rc=$?" >> /tmp/actions.log' EXIT
EOF
  chmod 644 "$T/_actlog.sh"
}

PY_SNIPPET='import os as _al_o, time as _al_t, atexit as _al_ae
def _al_log(ev):
    try:
        _n = _al_t.time()
        with open("/tmp/actions.log", "a") as _f:
            _f.write("%s.%03d %d %s a11y-dump\n" % (_al_t.strftime("%H:%M:%S", _al_t.localtime(_n)), int(_n * 1000) % 1000, _al_o.getpid(), ev))
    except Exception:
        pass
_al_log("start")
_al_ae.register(_al_log, "end")  # actlog'

do_install() {
  install_helper
  for n in $BASH_TOOLS; do
    f="$T/$n"; [ -f "$f" ] || continue
    grep -q "_actlog.sh" "$f" && continue
    [ -f "$f.prelog" ] || cp -p "$f" "$f.prelog"
    sed -i '1a . /opt/tools/_actlog.sh' "$f"
  done
  for n in $PY_TOOLS; do
    f="$T/$n"; [ -f "$f" ] || continue
    grep -q "# actlog" "$f" && continue
    [ -f "$f.prelog" ] || cp -p "$f" "$f.prelog"
    tmp=$(mktemp)
    { head -1 "$f"; printf '%s\n' "$PY_SNIPPET"; tail -n +2 "$f"; } > "$tmp"
    cat "$tmp" > "$f"; rm -f "$tmp"
  done
  echo "installed"
}

do_uninstall() {
  for n in $BASH_TOOLS $PY_TOOLS; do
    f="$T/$n"
    [ -f "$f.prelog" ] && cat "$f.prelog" > "$f" && rm -f "$f.prelog"
  done
  rm -f "$T/_actlog.sh"
  echo "uninstalled"
}

do_status() {
  for n in $BASH_TOOLS $PY_TOOLS; do
    f="$T/$n"; [ -f "$f" ] || continue
    if grep -q "_actlog.sh\|# actlog" "$f"; then s=on; else s=off; fi
    printf '%-20s %s\n' "$n" "$s"
  done
  [ -f "$LOG" ] && { echo "log lines: $(wc -l < "$LOG")"; tail -5 "$LOG"; }
}

case "${1:-status}" in
  install) do_install ;;
  uninstall) do_uninstall ;;
  *) do_status ;;
esac
