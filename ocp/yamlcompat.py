"""YAML loading - use PyYAML when importable, otherwise a bundled subset parser.

ocp must run under whichever python3 is on PATH. macOS ships PyYAML with
/usr/bin/python3 but Homebrew's python3 has no yaml module, and the system
python is externally managed so we cannot just pip install into it. Supporting
both removes that whole class of "works on my interpreter" failure.

The fallback handles what our config files actually use: nested maps, lists
(including lists of maps), comments, quoted strings, and int/float/bool/null.
Anything it does not understand (anchors, block scalars, flow collections)
raises ConfigError rather than silently mis-parsing. Stopping beats reading
a routing table wrong.
"""
from __future__ import annotations


class ConfigError(Exception):
    pass


try:
    import yaml as _yaml

    HAVE_PYYAML = True

    def load(text: str):
        try:
            return _yaml.safe_load(text) or {}
        except _yaml.YAMLError as e:
            raise ConfigError(str(e)) from e

except ImportError:
    HAVE_PYYAML = False

    def load(text: str):
        return parse(text)


_UNSUPPORTED = ("&", "*", "|", ">", "{", "[", "!!", "<<")


def _scalar(tok: str):
    t = tok.strip()
    if not t:
        return None
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    low = t.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    return t


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _rows(text: str):
    """Yield (line_number, indent, content) for lines that carry data."""
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip() or line.lstrip().startswith("---"):
            continue
        yield n, len(line) - len(line.lstrip()), line.strip()


def _check(n: int, body: str) -> None:
    """Reject syntax the subset parser would misread. Values only - keys may
    legitimately contain '-'."""
    for bad in _UNSUPPORTED:
        if bad in body:
            raise ConfigError(
                f"line {n}: bundled YAML parser does not support {bad!r}. "
                "Run ocp under a python3 that has PyYAML, or simplify the syntax."
            )


def parse(text: str):
    rows = list(_rows(text))
    if not rows:
        return {}
    value, idx = _block(rows, 0, rows[0][1])
    if idx != len(rows):
        raise ConfigError(f"line {rows[idx][0]}: inconsistent indentation")
    return value if value is not None else {}


def _block(rows, i: int, indent: int):
    if i >= len(rows):
        return None, i
    if rows[i][2].startswith("- "):
        return _seq(rows, i, indent)
    return _map(rows, i, indent)


def _seq(rows, i: int, indent: int):
    out = []
    while i < len(rows):
        n, ind, body = rows[i]
        if ind < indent or not body.startswith("- "):
            break
        if ind > indent:
            raise ConfigError(f"line {n}: list item indentation does not line up")
        rest = body[2:].strip()
        if ":" in rest and not rest.startswith(("\"", "'")):
            # The item is a map. Treat its first line as a map row at the
            # item's own indent, then absorb the more-indented lines under it.
            synth = [(n, indent + 2, rest)]
            j = i + 1
            while j < len(rows) and rows[j][1] > indent:
                synth.append(rows[j])
                j += 1
            item, k = _map(synth, 0, indent + 2)
            if k != len(synth):
                raise ConfigError(f"line {synth[k][0]}: could not parse list item")
            out.append(item)
            i = j
        else:
            _check(n, rest)
            out.append(_scalar(rest))
            i += 1
    return out, i


def _map(rows, i: int, indent: int):
    out = {}
    while i < len(rows):
        n, ind, body = rows[i]
        if ind < indent or body.startswith("- "):
            break
        if ind > indent:
            raise ConfigError(f"line {n}: unexpected indentation")
        if ":" not in body:
            raise ConfigError(f"line {n}: missing ':' in {body!r}")
        key, _, rest = body.partition(":")
        key = key.strip().strip("\"'")
        rest = rest.strip()
        if rest:
            _check(n, rest)
            out[key] = _scalar(rest)
            i += 1
        elif i + 1 < len(rows) and rows[i + 1][1] > indent:
            out[key], i = _block(rows, i + 1, rows[i + 1][1])
        elif i + 1 < len(rows) and rows[i + 1][1] == indent and rows[i + 1][2].startswith("- "):
            out[key], i = _seq(rows, i + 1, indent)
        else:
            out[key] = None
            i += 1
    return out, i
