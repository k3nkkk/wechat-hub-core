#!/bin/bash
# Experiment: serialize a11y-dump so WeChat's accessibility tree is never walked by two
# processes at once. Run INSIDE the agent container: bash a11ylock.sh install|uninstall|status
set -euo pipefail
F=${A11YLOCK_FILE:-/opt/tools/a11y-dump}
MARK="# a11ylock"

case "${1:-status}" in
  install)
    if grep -q "$MARK" "$F"; then echo "already installed"; exit 0; fi
    tmp=$(mktemp)
    {
      head -1 "$F"
      echo "import fcntl as _lk_f  $MARK"
      echo "_lk_fd = open('/tmp/a11y-dump.lock', 'w')  $MARK"
      echo "_lk_f.flock(_lk_fd, _lk_f.LOCK_EX)  $MARK"
      tail -n +2 "$F"
    } > "$tmp"
    cat "$tmp" > "$F"; rm -f "$tmp"
    echo "installed"
    ;;
  uninstall)
    sed -i "/$MARK/d" "$F"
    echo "uninstalled"
    ;;
  *)
    if grep -q "$MARK" "$F"; then echo "lock: on"; else echo "lock: off"; fi
    head -5 "$F"
    ;;
esac
