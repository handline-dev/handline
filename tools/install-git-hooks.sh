#!/usr/bin/env bash
# Point this repo, and every worktree of it, at tools/githooks. A worktree whose branch has no
# tools/githooks yet runs no hook until it takes main. Run once per clone.
cd "$(dirname "$0")/.." || exit 1
git config core.hooksPath tools/githooks && echo "core.hooksPath = tools/githooks"
