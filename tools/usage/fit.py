"""What each token class costs the plan: python3 tools/usage/fit.py [interval_s]
Regresses the rise of each account's 5h percentage over consecutive live
readings on the tokens of each class spent in between (db.token_calls, recorded
by the daemon), and bootstraps the split of fleet usage. Needs numpy."""
import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from handline import db  # noqa: E402
from handline.usage import tokenlog, windows  # noqa: E402

B = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
con = db.connect()
X, y = [], []
by = collections.defaultdict(list)
for r in con.execute("SELECT * FROM usage_samples ORDER BY account, ts"):
    by[r["account"]].append(r)
for a, rs in by.items():
    bk = {}
    for r in rs:
        w = (windows.entries(dict(r)) or [{}])[0]
        if (w.get("used_percent") is not None and w.get("readable") is not False
                and not (w["carried"] or w["lost"])):
            bk[r["ts"] // B] = r
    ks = sorted(bk)
    for k0, k1 in zip(ks, ks[1:]):
        p, q = bk[k0], bk[k1]
        # Adjacent buckets in one window, and the figure did not fall.
        pw, qw = windows.entries(dict(p))[0], windows.entries(dict(q))[0]
        if k1 != k0 + 1 or pw["resets_at"] != qw["resets_at"] or qw["used_percent"] < pw["used_percent"]:
            continue
        t = tokenlog.totals(con, a, p["ts"], q["ts"])
        X.append([t["cc"], t["cr"], t["out"]])
        y.append(qw["used_percent"] - pw["used_percent"])
X, y = np.array(X, float), np.array(y, float)
if len(y) < 10:
    sys.exit(f"{len(y)} intervals: too few to fit")
tot = np.array(X.sum(axis=0))
if not tot.any():
    sys.exit("no token_calls recorded yet: the daemon records them from its next tick")
w = np.linalg.lstsq(X, y, rcond=None)[0]
r2 = 1 - ((y - X @ w) ** 2).sum() / ((y - y.mean()) ** 2).sum()
print(f"{len(y)} intervals, R2 {r2:.3f}; % of a 5h window per M tokens: "
      + ", ".join(f"{n} {v * 1e6:.2f}" for n, v in zip(("cache write", "cache read", "output"), w)))
rng = np.random.default_rng(1)
shares = []
for _ in range(2000):
    i = rng.integers(0, len(y), len(y))
    wi = np.clip(np.linalg.lstsq(X[i], y[i], rcond=None)[0], 0, None) * tot
    if wi.sum() > 0:
        shares.append(wi / wi.sum())
shares = np.array(shares)
for j, n in enumerate(("cache write", "cache read", "output")):
    lo, mid, hi = np.percentile(shares[:, j], [5, 50, 95])
    print(f"{n}: {mid:.0%} of usage (90% interval {lo:.0%}-{hi:.0%})")
