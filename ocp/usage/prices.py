"""What a token costs, by model and by kind, so a share of an account's tokens can
be a share of what those tokens cost (T45).

Only ratios enter a share: a cache read is a fraction of an input token and an
output token five, and an Opus 5.5 token twice a Sonnet 5 one. Input prices are
Anthropic's list prices in USD per million tokens (the claude-api reference,
2026-06-24); output is 5x the input price on every model here, a cache write 1.25x
(5-minute), and a cache read 0.1x except where a model's own price says otherwise
(`CACHE_READ`).

A model is priced by the first pattern in the table that its id contains, so the
table runs most specific first: `opus-5-5` before `opus`, `sonnet-5` before
`sonnet`. A model that matches none is priced as `DEFAULT`. `token_prices:` in
config.yaml (`{opus-5-5: 4, fable: 12}`) sets the input price of a pattern, and a
pattern the table lacks is tried before all of it.
"""
from __future__ import annotations

# Pattern in the model id -> USD per million input tokens, most specific first.
INPUT_PRICE = {
    "fable-5-1": 10.0,    # Fable 5.1
    "mythos-5-1": 10.0,   # Mythos 5.1, the Fable 5.1 price
    "fable": 10.0,        # Fable 5
    "mythos": 10.0,       # Mythos 5
    "opus-5-5": 4.0,
    "opus": 5.0,          # Opus 5, 4.x
    "sonnet-5": 2.0,
    "sonnet": 3.0,        # Sonnet 4.x
    "haiku": 1.0,
}
DEFAULT = "sonnet"

# A cache read as a multiple of the input price, where it is not 0.1x: Opus 5.5
# reads at $0.20 of its $4, Fable 5.1 and Mythos 5.1 at $0.25 of their $10. Fable 5
# and Mythos 5 read at the usual 0.1x (the claude-api reference gives $0.25 as new
# with 5.1).
CACHE_READ = {"opus-5-5": 0.05, "fable-5-1": 0.025, "mythos-5-1": 0.025}

# Each kind's price as a multiple of the model's input price. Thinking is
# billed as output, and is part of `out` in the log (`output_tokens_details`):
# it is priced by its own multiple, and `class_costs` takes it out of `out` first.
KIND = {"inp": 1.0, "cc": 1.25, "cr": 0.1, "out": 5.0, "thinking": 5.0}


def pattern_of(model: str, table: dict[str, float] | None = None) -> str:
    """The first pattern of `table` (default `INPUT_PRICE`) that `model` contains,
    else the default one."""
    m = (model or "").lower()
    prices = table or INPUT_PRICE
    return next((p for p in prices if p in m), DEFAULT)


def table_from_config(cfg: dict) -> dict[str, float]:
    """`INPUT_PRICE` with config.yaml's `token_prices` laid over it: a pattern the
    table has takes the new price where it is, one it lacks goes first. An entry
    that is not a positive number is ignored."""
    out = dict(INPUT_PRICE)
    given = cfg.get("token_prices")
    if isinstance(given, dict):
        added: dict[str, float] = {}
        for pat, v in given.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                (out if str(pat) in out else added)[str(pat)] = float(v)
        out = {**added, **out}
    return out


def class_costs(model: str, inp: int, cc: int, cr: int, out: int, thinking: int,
                table: dict[str, float] | None = None) -> dict[str, float]:
    """USD each kind of these tokens costs at list price. Thinking tokens are a
    part of `out`, so `out` here is what is left of it and they are priced once,
    as thinking."""
    prices = table or INPUT_PRICE
    pat = pattern_of(model, prices)
    base = prices.get(pat, INPUT_PRICE[DEFAULT])
    mult = {**KIND, "cr": CACHE_READ.get(pat, KIND["cr"])}
    think = min(thinking, out)
    counts = {"inp": inp, "cc": cc, "cr": cr, "out": out - think, "thinking": think}
    return {k: n * mult[k] * base / 1_000_000 for k, n in counts.items()}


def cost(model: str, inp: int, cc: int, cr: int, out: int, thinking: int,
         table: dict[str, float] | None = None) -> float:
    return sum(class_costs(model, inp, cc, cr, out, thinking, table).values())
