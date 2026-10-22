#!/usr/bin/env bash
# Installs tmux on a CI runner: the terminal tests drive a real one. A mirror that stalls is dropped after 20 s and retried, and the
# whole install gives up after 3 tries, so one slow mirror costs minutes, not the job's 30.
command -v tmux >/dev/null && exit 0
apt=(sudo apt-get -qq -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 -o Acquire::Retries=3)
for try in 1 2 3; do
    timeout 180 "${apt[@]}" update && timeout 180 "${apt[@]}" install -y --no-install-recommends tmux && exit 0
    echo "tools/ci-tmux.sh: try $try failed" >&2
done
exit 1
