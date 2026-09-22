"""YAML loading - use PyYAML when importable, otherwise a bundled subset parser.

ocp must run under whichever python3 is on PATH. macOS ships PyYAML with
/usr/bin/python3 but Homebrew's python3 has no yaml module, and the system
python is externally managed so we cannot just pip install into it.

The rule for the fallback is that it must never disagree with PyYAML *silently*.
Two parsers that quietly read the same routing policy differently is worse than
having no fallback at all - `creates_gate: no` meaning True under one and False
under the other decides whether an escalation needs a human. So anything the
fallback is not sure it reads the same way raises ConfigError instead of
guessing. tests/test_ocp.py compares the two on a fixed list of sources and
fails if it cannot find a python3 that has PyYAML to compare against.

Supported: nested maps, lists (including lists of maps), comments, quoted
strings, flow collections, and YAML 1.1 booleans (yes/no/on/off/true/false in
the spellings PyYAML takes). Rejected loudly: anchors, block scalars, merge
keys, tabs, multiple documents, unbalanced flow collections, sexagesimals like
12:30, dates, inf and nan, numbers written with underscores, escapes in
double quotes other than \\ and \", and a plain key PyYAML would not read as
text (1, on, null, a date).
"""
from __future__ import annotations

import re

from . import textsafe


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


_UNSUPPORTED = ("&", "*", "|", ">", "!!", "<<")

# YAML 1.1 booleans, which PyYAML's safe_load accepts. Missing these is how
# `creates_gate: no` became the string "no", which is truthy.
#
# PyYAML's resolver takes exactly these spellings, so "yEs" and "y" are strings
# to it; parsing them as booleans here would be a silent disagreement.
_PY_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_PY_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_PY_NULL = {"null", "Null", "NULL", "~"}
# What `dump` quotes: anything a YAML 1.1 reader might take for one of them,
# in any case, including y and n.
_TRUE = {"true", "yes", "on", "y"}
_FALSE = {"false", "no", "off", "n"}

# Sexagesimal (12:30 -> 750) and similar legacy forms. We do not implement
# them; we refuse them, so we cannot disagree with PyYAML about them.
_SEXAGESIMAL = re.compile(r"^[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?$")

# These mirror PyYAML's own resolver, because anywhere they differ we would
# silently disagree with it. Notably YAML 1.1 has no "0o" octal (that is a
# string), and an exponent needs an explicit sign and a decimal point, so
# "1e3" is a string while "1.0e+3" is a float.
_HEX = re.compile(r"^[-+]?0x[0-9a-fA-F]+$")
_BIN = re.compile(r"^[-+]?0b[01]+$")
_OCT = re.compile(r"^[-+]?0[0-7]+$")
_DEC = re.compile(r"^[-+]?(0|[1-9][0-9]*)$")
# A sign goes only before digits: "-.5" is a string to PyYAML, ".5" a float.
_FLOAT = re.compile(r"^([-+]?\d+\.\d*|\.\d+)([eE][-+]\d+)?$")
# What PyYAML reads as a number or a timestamp and this parser does not: the
# underscore forms (1_000, -1_0.5, 0x_1F), .inf and .nan in the spellings it
# takes, and dates. Each is refused, because returning the text would disagree with a
# reader that returns an int, an inf or a date.
_PY_NUMBER = re.compile(
    r"^(?:[-+]?0b[01_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?)$")
_INF_NAN = re.compile(r"^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_TIMESTAMP = re.compile(
    r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$")


def _closes_at_end(t: str) -> bool:
    """Whether the quote `t` opens with is first closed by its last character."""
    quote, i = t[0], 1
    while i < len(t):
        ch = t[i]
        if quote == '"' and ch == "\\":
            i += 2
            continue
        if ch == quote:
            if quote == "'" and t[i + 1:i + 2] == "'":
                i += 2
                continue
            return i == len(t) - 1
        i += 1
    return False


def _unquote(t: str, line: int = 0) -> str:
    """The text of a quoted scalar. Single quotes double a quote to write one;
    double quotes here know only the two escapes `dump` writes, since any other
    is one this parser would read differently from PyYAML."""
    body = t[1:-1]
    if t[0] == "'":
        return body.replace("''", "'")
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if body[i + 1:i + 2] not in ('"', "\\"):
                raise ConfigError(f"line {line}: escape {body[i:i + 2]!r} in {t!r} is not supported; "
                                  "use single quotes")
            i += 1
            ch = body[i]
        out.append(ch)
        i += 1
    return "".join(out)


def _key(raw: str, line: int = 0) -> str:
    """A map key, which is always text here. A quoted key is its text. A plain
    one is refused unless the value rules would return it unchanged: PyYAML
    reads `1:`, `on:`, `null:` and a date as an int, a bool, None and a date,
    and a config key that is one of those is a mistake to quote, not a key to
    hand on as something else."""
    k = raw.strip()
    if k[:1] in ("\"", "'"):
        if not _closes_at_end(k):
            raise ConfigError(f"line {line}: quoted key is not closed at its end: {k!r}")
        return _unquote(k, line)
    # An anchor, alias, tag, block indicator or merge key is syntax only where
    # the key starts; `a>b` is text to PyYAML.
    bad = next((u for u in _UNSUPPORTED if k.startswith(u)), None)
    if bad:
        raise ConfigError(f"line {line}: key {k!r} starts with {bad!r}, which is YAML syntax "
                          "(an anchor, alias, tag, block scalar or merge key); "
                          "quote it if you mean the text")
    got = _scalar(k, line)
    if not isinstance(got, str) or got != k:
        raise ConfigError(f"line {line}: key {k!r} is read as {got!r} by PyYAML; "
                          "quote it if you mean the text")
    return k


def _scalar(tok: str, line: int = 0):
    t = tok.strip()
    if not t:
        return None
    if (t.startswith("[") and t.endswith("]")) or (t.startswith("{") and t.endswith("}")):
        return _flow(t, line)
    if t.startswith(("[", "{")) or t.endswith(("]", "}")):
        raise ConfigError(f"line {line}: unbalanced flow collection: {t!r}")
    if t[0] in "\"'":
        # A scalar that opens a quote is that quoted scalar and nothing else:
        # PyYAML refuses an unterminated one and text after the closing quote.
        if not _closes_at_end(t):
            raise ConfigError(f"line {line}: quoted scalar is not closed at its end: {t!r}")
        return _unquote(t, line)

    low = t.lower()
    if t in _PY_NULL:
        return None
    if t in _PY_TRUE:
        return True
    if t in _PY_FALSE:
        return False
    # PyYAML reads the first group as strings and the rest as inf, nan, ints,
    # floats and dates. Refuse rather than differ.
    if (low in ("inf", "-inf", "+inf", "nan") or _INF_NAN.match(t)
            or ("_" in t and _PY_NUMBER.match(t)) or _TIMESTAMP.match(t)):
        raise ConfigError(
            f"line {line}: {t!r} is ambiguous across YAML parsers; quote it if you mean the string"
        )
    if _SEXAGESIMAL.match(t):
        raise ConfigError(
            f"line {line}: {t!r} is a YAML 1.1 sexagesimal; quote it if you mean the string"
        )
    if _HEX.match(t):
        return int(t, 16)
    if _BIN.match(t):
        return int(t, 2)
    if _OCT.match(t):
        return int(t, 8)
    if _DEC.match(t):
        return int(t)
    if _FLOAT.match(t):
        return float(t)
    # An unquoted bracket that did not form a flow collection is a typo, not a
    # string. Returning it as text is how `- { from: "a"` became a list of one
    # string instead of an error.
    if any(c in t for c in "[]{}"):
        raise ConfigError(f"line {line}: unbalanced or stray bracket in value: {t!r}")
    return t


def _split_flow(body: str, line: int = 0) -> list[str]:
    """Split a flow collection body on commas at nesting depth 0."""
    parts, buf, depth, quote, escaped = [], [], 0, None, False
    for ch in body:
        if quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quote:
        raise ConfigError(f"line {line}: unterminated quote in flow collection: {body!r}")
    if depth != 0:
        raise ConfigError(f"line {line}: unbalanced brackets in flow collection: {body!r}")
    tail = "".join(buf)
    if tail.strip() or parts:
        parts.append(tail)
    return [p.strip() for p in parts]


def _flow(t: str, line: int = 0):
    """Parse [a, b] and {k: v}, including nesting."""
    if t.startswith("["):
        return [_scalar(p, line) for p in _split_flow(t[1:-1], line) if p != ""]
    out = {}
    for item in _split_flow(t[1:-1], line):
        if not item:
            continue
        split = _split_key(item)
        if not split:
            raise ConfigError(f"line {line}: flow map entry is not 'key: value': {item!r}")
        k, v = split
        out[_key(k, line)] = _scalar(v.strip(), line)
    return out


def _split_key(item: str) -> tuple[str, str] | None:
    """Split 'key: value' at the first structural colon.

    A colon only separates a key from a value when followed by whitespace or at
    end of line. Splitting on a bare ':' turned the list item `- a:b` into the
    map {'a': 'b'} and `- http://example.com` into {'http': '//example.com'}.
    Colons inside quotes never separate.
    """
    quote, escaped = None, False
    for i, ch in enumerate(item):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(item) or item[i + 1] in " \t"):
            return item[:i], item[i + 1:]
    return None


def _strip_comment(line: str) -> str:
    """Remove a trailing comment.

    A '#' only starts a comment at the start of the line or after whitespace.
    Treating every '#' as a comment turned `reason: go#1` into 'go' and
    truncated any URL with a fragment.
    """
    out, quote, escaped = [], None, False
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _rows(text: str):
    """Yield (line_number, indent, content) for lines that carry data."""
    emitted = False
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"line {n}: tab used for indentation; YAML forbids it")
        if raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("---"):
            # A leading '---' opens the single document. One that appears after
            # data starts a second document, which PyYAML rejects - so do we,
            # rather than silently merging the two.
            if emitted:
                raise ConfigError(f"line {n}: multiple YAML documents; ocp reads exactly one")
            continue
        if stripped == "...":
            continue
        emitted = True
        yield n, len(line) - len(line.lstrip()), stripped


def _outside_quotes(body: str) -> str:
    """`body` with its quoted scalars removed: what is inside quotes is text
    whatever it holds, so only what is outside can be syntax."""
    out, quote, escaped = [], None, False
    for ch in body:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        else:
            out.append(ch)
    return "".join(out)


def _check(n: int, body: str) -> None:
    outside = _outside_quotes(body)
    for bad in _UNSUPPORTED:
        if bad in outside:
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
    if rows[i][2].startswith("- ") or rows[i][2] == "-":
        return _seq(rows, i, indent)
    return _map(rows, i, indent)


def _seq(rows, i: int, indent: int):
    out = []
    while i < len(rows):
        n, ind, body = rows[i]
        if ind < indent or not (body.startswith("- ") or body == "-"):
            break
        if ind > indent:
            raise ConfigError(f"line {n}: list item indentation does not line up")
        rest = body[2:].strip() if body.startswith("- ") else ""
        if rest.startswith(("[", "{")):
            out.append(_scalar(rest, n))
            i += 1
            continue
        split = _split_key(rest) if rest else None
        if split:
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
        elif rest:
            _check(n, rest)
            out.append(_scalar(rest, n))
            i += 1
        else:
            child, i = _block(rows, i + 1, rows[i + 1][1]) if i + 1 < len(rows) else (None, i + 1)
            out.append(child)
    return out, i


def _map(rows, i: int, indent: int):
    out = {}
    while i < len(rows):
        n, ind, body = rows[i]
        if ind < indent or body.startswith("- ") or body == "-":
            break
        if ind > indent:
            raise ConfigError(f"line {n}: unexpected indentation")
        split = _split_key(body)
        if not split:
            raise ConfigError(f"line {n}: expected 'key: value' but found {body!r}")
        key, rest = split
        key = _key(key, n)
        rest = rest.strip()
        if rest:
            _check(n, rest)
            out[key] = _scalar(rest, n)
            i += 1
        elif i + 1 < len(rows) and rows[i + 1][1] > indent:
            out[key], i = _block(rows, i + 1, rows[i + 1][1])
        elif i + 1 < len(rows) and rows[i + 1][1] == indent and (
            rows[i + 1][2].startswith("- ") or rows[i + 1][2] == "-"
        ):
            out[key], i = _seq(rows, i + 1, indent)
        else:
            out[key] = None
            i += 1
    return out, i


def is_plain_text(name: str) -> bool:
    """Whether `name` is read back as itself wherever ocp writes one unquoted:
    as a block key, as a value, as a list item and as a flow key. It is the test
    for a name before it is written into a file, because a name that fails it
    would make the file refuse to load, or load as something else."""
    shapes = ((f"{name}: 1\n", {name: 1}), (f"k: {name}\n", {"k": name}),
              (f"k: [{name}]\n", {"k": [name]}), (f"k: {{ {name}: 1 }}\n", {"k": {name: 1}}))
    for src, want in shapes:
        try:
            if parse(src) != want:
                return False
        except ConfigError:
            return False
    return True


def _reads_back_as_other(text: str) -> bool:
    """Whether the plain text of a string would parse as anything but itself."""
    try:
        return _scalar(text) != text
    except ConfigError:
        return True


def _float_text(value: float) -> str:
    """A float as YAML 1.1 reads it back as a float: PyYAML takes an exponent
    only after a decimal point, so 1e+16 is written 1.0e+16. inf and nan have
    no form both readers return as the value."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ConfigError(f"dump does not write {value!r}: no form both parsers read back")
    text = repr(value)
    mantissa, e, exponent = text.partition("e")
    return text if "." in mantissa or not e else f"{mantissa}.0e{exponent}"


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first_unsafe_char(value: str) -> str | None:
    """`textsafe.UNSAFE_IN_VALUE` is the definition, shared with `agents`'
    name check and with the driver values `spawn` parses - a config scalar is
    the same kind of thing, stored and handed on. dump() below refuses writing
    a value that carries one; refuse_unsafe_values refuses reading one back,
    the read side of the same rule."""
    return next((c for c in value if c in textsafe.UNSAFE_IN_VALUE), None)


def refuse_unsafe_values(data: dict, source) -> None:
    """Refuse a parsed map that carries a value dump() would have refused to
    write - the read side of the same rule, not a second one.

    Only top-level string values: dump() only ever writes those (a nested
    value is refused there too, before this ever runs against it), so a
    string nested inside a list or a map dump() could not have produced is
    not this function's to check either. `source` is named in the error so
    the daemon's stall (#1571's guard, now the thing that makes this REFUSE
    rather than STRIP safe) says which file to go and look at, not just that
    something, somewhere, was bad.
    """
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        bad = _first_unsafe_char(value)
        if bad is not None:
            raise ConfigError(f"{source}: key {key!r} carries a control character "
                              f"({hex(ord(bad))}) - refused, not read as though it "
                              f"were not there")


def dump(data: dict) -> str:
    """Serialize a shallow config map back to YAML.

    Deliberately limited to what ocp writes: agent.yaml, which is a flat map of
    scalars. Anything nested is rejected rather than emitted in a form the
    subset parser might not read back the same way. Every line is read back
    through `parse` before it is kept, so a value the read path would refuse or
    change is an error here, at the write, and not a file that fails to load.
    """
    if not isinstance(data, dict):
        raise ConfigError("dump expects a map")
    lines = []
    for key, value in data.items():
        if isinstance(value, dict) or isinstance(value, list):
            raise ConfigError(f"dump does not write nested values (key {key!r})")
        candidates = []
        if value is None:
            candidates.append("null")
        elif isinstance(value, bool):
            candidates.append("true" if value else "false")
        elif isinstance(value, int):
            candidates.append(str(value))
        elif isinstance(value, float):
            candidates.append(_float_text(value))
        else:
            value = str(value)
            # `_first_unsafe_char` is the one definition, shared with
            # refuse_unsafe_values: 0x9b is CSI, one byte, and a value
            # carrying it once reached agent.yaml unquoted and was drawn on
            # the bridge.
            if _first_unsafe_char(value) is not None:
                raise ConfigError(f"dump does not write control characters (key {key!r})")
            # Quote anything a reader would take for something else, or that
            # carries structure. A string this parser would not read back as
            # itself is one PyYAML may not either, so that test is the rule;
            # the leading characters are the YAML indicators, which PyYAML
            # refuses at the start of a plain scalar.
            needs_quotes = (
                value.strip() != value
                or value == ""
                or value.lower() in _TRUE | _FALSE | {"null", "~"}
                or value[0] in "-?!%@`=" or value.startswith(_UNSUPPORTED)
                or any(c in value for c in ":#[]{},&*|>'\"")
                or _reads_back_as_other(value)
            )
            candidates = [_quoted(value)] if needs_quotes else [value, _quoted(value)]
        for rendered in candidates:
            line = f"{key}: {rendered}"
            try:
                back = parse(line + "\n")
            except ConfigError:
                continue
            if isinstance(back, dict) and repr(back.get(key)) == repr(value):
                lines.append(line)
                break
        else:
            raise ConfigError(f"dump cannot write {key!r}: the parser does not read "
                              f"{candidates[-1]!r} back as {value!r}")
    return "\n".join(lines) + "\n"
