#!/usr/bin/env bash
# Puts tmux on a CI runner: the terminal tests drive a real one. The runner's package mirror sometimes hangs without a word, so each apt
# call has its own time limit and retries, and a hang fails in minutes with a reason instead of using up the job's 30 minutes.
command -v tmux >/dev/null && exit 0
apt=(sudo timeout 120 apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
"${apt[@]}" update -qq || { echo "::error::apt-get update did not finish within 120 s: the runner's package mirror; rerun the job"; exit 1; }
"${apt[@]}" install -y --no-install-recommends tmux || { echo "::error::installing tmux did not finish within 120 s; rerun the job"; exit 1; }
