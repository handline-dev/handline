#!/usr/bin/env python3
"""The pull request checks CI runs beside the tests (.github/workflows/check.yml and pr-rules.yml):

    python3 tools/pr_checks.py files BASE   the files the change adds or edits: no binary or image outside the asset paths,
                                            no scratch file, no comment block over MAX_COMMENT_LINES with a line
                                            the change adds or edits
    python3 tools/pr_checks.py body         the pull request in $GITHUB_EVENT_PATH: base, body sections, no Korean and
                                            no message ids

Each prints one line per problem and exits 1 when there is one."""
import json
import os
import re
import subprocess
import sys

MAX_COMMENT_LINES = 6
MAX_BINARY_BYTES = 1 << 20
# Screenshots the phone's e2e flows keep, and the apps' own icons and fonts. Images for a review go in the PR body.
ASSET_PATHS = ("apps/mobile/shots/", "apps/mobile/assets/", "apps/web/public/", "apps/electron/assets/")
MEDIA = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?|heic|ico|icns|svg|mp4|mov|webm|ttf|otf|woff2?)$", re.I)
SCRATCH = re.compile(r"(^|/)(scratch|tmp|\.claude)/|(^|/)\.DS_Store$|\.(orig|rej|bak|swp|log)$|~$")
GENERATED = ("contract/protocol.ts", "contract/protocol.md")
CODE = {".ts": "//", ".tsx": "//", ".js": "//", ".mjs": "//", ".go": "//", ".py": "#", ".sh": "#"}
SECTIONS = ("Why", "What changed", "Evidence", "Risk")
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
MESSAGE_ID = re.compile(r"\bmsg_[0-9a-f]{7}\b|\bocp-msg [0-9a-f]{8,}")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def comment_blocks(lines: list[str], mark: str) -> list[tuple[int, int]]:
    """(first line, last line), 1-based, of each run of comment-only lines."""
    out, start, in_block = [], None, False
    for i, line in enumerate(lines + [""], 1):
        s = line.strip()
        opens = mark == "//" and s.startswith("/*")
        is_comment = s.startswith(mark) and not s.startswith("#!") or opens or in_block
        in_block = (opens or in_block) and "*/" not in s
        if is_comment and start is None:
            start = i
        elif not is_comment and start is not None:
            out.append((start, i - 1))
            start = None
    return out


def added_lines(base: str, head: str, path: str) -> set[int]:
    got = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git("diff", "-U0", "--no-renames", f"{base}...{head}", "--", path), re.M):
        first, count = int(m.group(1)), int(m.group(2) or 1)
        got.update(range(first, first + count))
    return got


def file_problems(base: str, head: str = "HEAD") -> list[str]:
    out = []
    for row in git("diff", "--numstat", "--no-renames", "--diff-filter=AM", f"{base}...{head}").splitlines():
        added, _, path = row.split("\t", 2)
        allowed = path.startswith(ASSET_PATHS)
        if SCRATCH.search(path):
            out.append(f"{path}: a scratch file; keep it out of the change")
        elif MEDIA.search(path) and not allowed:
            out.append(f"{path}: an image or media file outside {', '.join(ASSET_PATHS)}; put review images in the PR body")
        elif added == "-":
            size = int(git("cat-file", "-s", f"{head}:{path}"))
            if not allowed or size > MAX_BINARY_BYTES:
                out.append(f"{path}: a binary file ({size} bytes)")
        ext = os.path.splitext(path)[1]
        if ext in CODE and path not in GENERATED and added != "-":
            lines = git("show", f"{head}:{path}").split("\n")
            new = added_lines(base, head, path)
            for first, last in comment_blocks(lines, CODE[ext]):
                if last - first + 1 > MAX_COMMENT_LINES and new & set(range(first, last + 1)):
                    out.append(f"{path}:{first}: a comment of {last - first + 1} lines (at most {MAX_COMMENT_LINES}); say it shorter")
    return out


def body_problems(pr: dict, files: list[str]) -> list[str]:
    out = []
    base = pr["base"]["ref"]
    if base != "main" and not base.startswith("story/"):
        out.append(f"the base is {base}: open the PR on main or a story/* branch")
    body = pr.get("body") or ""
    for name in SECTIONS:
        m = re.search(rf"^#{{2,4}}\s*{name}\s*$\n(.*?)(?=^#{{1,4}}\s|\Z)", body, re.M | re.S | re.I)
        if not m or not m.group(1).strip():
            out.append(f"the body has no '## {name}' section with text under it")
    text = (pr.get("title") or "") + "\n" + body
    if HANGUL.search(text):
        out.append("the title or body has Korean: write it in English")
    if MESSAGE_ID.search(text):
        out.append("the title or body names a message id: say what it asked instead")
    return out


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "files":
        problems = file_problems(argv[2])
    elif len(argv) == 2 and argv[1] == "body":
        pr = json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]
        problems = body_problems(pr, git("diff", "--name-only", f"{pr['base']['sha']}...HEAD").split())
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
