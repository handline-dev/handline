"""What a token costs, by model and by kind, so a share of an account's tokens can
be a share of what those tokens cost (T45).

Only ratios enter a share: a cache read is a tenth of an input token and an
output token five, and an Opus token dearer than a Sonnet one. The per-family
input prices are Anthropic's list prices in USD per million tokens; the kind
multipliers are its fixed ratios to that price (cache write 1.25x, cache read
0.1x, output 5x). A model that names no family is priced as `DEFAULT_FAMILY`.
`token_prices:` in config.yaml (`{sonnet: 3, opus: 5}`) overrides a family's
input price, for a price that has changed or a model this table does not know.
"""
from __future__ import annotations

# USD per million input tokens.
INPUT_PRICE = {"opus": 5.0, "sonnet": 3.0, "haiku": 1.0}
DEFAULT_FAMILY = "sonnet"

# Each kind's price as a multiple of the model's input price. Thinking is
# billed as output, and is part of `out` in the log (`output_tokens_details`):
# it is priced by its own multiple, and `cost` takes it out of `out` first.
KIND = {"inp": 1.0, "cc": 1.25, "cr": 0.1, "out": 5.0, "thinking": 5.0}


def family(model: str) -> str:
    m = (model or "").lower()
    return next((f for f in INPUT_PRICE if f in m), DEFAULT_FAMILY)


def table_from_config(cfg: dict) -> dict[str, float]:
    """`INPUT_PRICE` with config.yaml's `token_prices` laid over it; an entry
    that is not a positive number is ignored."""
    out = dict(INPUT_PRICE)
    given = cfg.get("token_prices")
    if isinstance(given, dict):
        for fam, v in given.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                out[str(fam)] = float(v)
    return out


def class_costs(model: str, inp: int, cc: int, cr: int, out: int, thinking: int,
                table: dict[str, float] | None = None) -> dict[str, float]:
    """USD each kind of these tokens costs at list price. Thinking tokens are a
    part of `out`, so `out` here is what is left of it and they are priced once,
    as thinking."""
    prices = table or INPUT_PRICE
    base = prices.get(family(model), prices.get(DEFAULT_FAMILY, INPUT_PRICE[DEFAULT_FAMILY]))
    think = min(thinking, out)
    counts = {"inp": inp, "cc": cc, "cr": cr, "out": out - think, "thinking": think}
    return {k: n * KIND[k] * base / 1_000_000 for k, n in counts.items()}


def cost(model: str, inp: int, cc: int, cr: int, out: int, thinking: int,
         table: dict[str, float] | None = None) -> float:
    return sum(class_costs(model, inp, cc, cr, out, thinking, table).values())
