#!/usr/bin/env python3
"""Future-swap audit for the intraday rules. The SQL audit never covered them.

    python3 tests/test_no_lookahead_intraday.py

THE TEST

A causal rule's decision at bar i can only use bars 0..i. So take a real session,
REPLACE bars i+1 onward with bars from a different session, and the signals at
bars 0..i must be identical. If they change, the rule read the future.

WHY SWAP RATHER THAN TRUNCATE. The first version of this test truncated the
session instead, and it was wrong twice over:

  1. Truncating changes len(s), so rules with length guards ("if len(s) < 5")
     return an empty mask and the test read that as lookahead. Four rules failed
     for that reason alone and none of them were actually broken.
  2. Worse, it PASSED volume_climax_long, the one rule already proven
     contaminated, because that rule fires rarely and never fired in the sampled
     sessions. All-False matched all-False and the test reported success.

Swapping keeps the session length fixed, which removes the first problem. The
second is handled by REQUIRING COVERAGE: a rule that never fires is reported
INCONCLUSIVE, never PASS. A test that cannot fail on a known bug is worse than no
test, because it manufactures confidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import intraday_signals as isg

DATA = ROOT / "data" / "intraday_1h.parquet"
N_TRIALS = 400
MIN_FIRES = 20       # below this the rule is INCONCLUSIVE, not PASS
SEED = 20260101


def main() -> int:
    if not DATA.exists():
        print(f"missing {DATA}; run python/ingest_intraday.py first")
        return 1
    d = pd.read_parquet(DATA)
    d = d[d["bars_in_session"] == 7].sort_values(["symbol", "ts"])
    cols = ["open", "high", "low", "close", "volume"]
    keys = d[["symbol", "session"]].drop_duplicates().reset_index(drop=True)

    rng = np.random.default_rng(SEED)
    # Pre-pick pairs of sessions: one to test, one to donate a fake future.
    idx = rng.choice(len(keys), size=(N_TRIALS, 2), replace=True)
    frames = {}

    def get(i):
        if i not in frames:
            sym, ses = keys.iloc[i]
            g = d[(d["symbol"] == sym) & (d["session"] == ses)]
            frames[i] = (sym, ses, g.reset_index(drop=True))
        return frames[i]

    print(f"future-swap audit: {N_TRIALS} session pairs, "
          f"{len(isg.RULES)} rules\n")
    results = {}
    for rule, fn in isg.RULES.items():
        needs_pc = rule in isg.NEEDS_PREV_CLOSE
        fires = 0
        changed = 0
        detail = None
        for a_i, b_i in idx:
            if a_i == b_i:
                continue
            sym, ses, A = get(int(a_i))
            _, _, B = get(int(b_i))
            if len(A) != 7 or len(B) != 7:
                continue
            pc = float(A["close"].iloc[0])
            real = np.asarray(fn(A, pc) if needs_pc else fn(A), dtype=bool)
            fires += int(real.any())
            # Cut point: everything after `k` gets someone else's bars.
            for k in range(1, 6):
                F = A.copy()
                F.loc[k + 1:, cols] = B.loc[k + 1:, cols].to_numpy()
                fake = np.asarray(fn(F, pc) if needs_pc else fn(F), dtype=bool)
                if not np.array_equal(real[: k + 1], fake[: k + 1]):
                    changed += 1
                    if detail is None:
                        detail = (sym, ses, k,
                                  real[: k + 1].astype(int).tolist(),
                                  fake[: k + 1].astype(int).tolist())
                    break
        results[rule] = (fires, changed, detail)

    npass = nfail = ninc = 0
    for rule, (fires, changed, detail) in results.items():
        if changed:
            nfail += 1
            print(f"  [FAIL] {rule}: decision changed on {changed} pairs when "
                  f"the future was swapped  ({fires} firings)")
            sym, ses, k, a, b = detail
            print(f"         {sym} {ses}, real future vs swapped after bar {k}")
            print(f"         real    {a}")
            print(f"         swapped {b}")
        elif fires < MIN_FIRES:
            ninc += 1
            print(f"  [INCONCLUSIVE] {rule}: only {fires} firings, too few to "
                  f"prove anything")
        else:
            npass += 1
            print(f"  [PASS] {rule}  ({fires} firings)")

    print(f"\n{npass} causal, {nfail} reading the future, {ninc} inconclusive")
    if nfail:
        print("\nA rule whose decision changes when the future is swapped was")
        print("using the future to decide. Every result from it is void.")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
