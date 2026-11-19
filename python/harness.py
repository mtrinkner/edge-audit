"""Automatic look-ahead checks for any study written against decide(view, book).
See docs/INFRASTRUCTURE.md; tests in tests/test_pit_harness.py.

- truncation: drop every row known after the cut; decisions up to the cut
  must be identical.
- scramble: permute the values of every row whose event happened after the
  cut; decisions up to the cut must be identical. This catches a known_at
  set too early, which truncation can't see.
- sealing: decide() runs under pit.sealed(), so a study that reads a file
  fails outright.

Datasets registered with `lookahead` (a study's declared conditioning set)
are listed in the result and left as they are; every other dataset is tested.
What the harness can't see: data a study holds outside the view (a global
loaded at import). Studies hold none, and review checks for it.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

import pit as P
import sim as S


class Recorder:
    """Wraps a study and records every order it places, by day."""

    def __init__(self, study):
        self.study, self.orders = study, []

    def decide(self, view, book):
        out = self.study.decide(view, book) or []
        day = str(view.t.date())
        for o in out:
            self.orders.append((day, o.key, o.side, o.qty, o.lot, o.fill, o.good_from, o.good_until, o.tag))
        return out


def decisions(make_study: Callable, pit: P.PIT, start: str, end: str, sim_kw: dict | None = None) -> list[tuple]:
    rec = Recorder(make_study(pit))
    S.Sim(pit, **(sim_kw or {})).run(rec, start, end)
    return rec.orders


def check_one(make_study: Callable, pit: P.PIT, start: str, cut: str, sim_kw: dict | None = None,
              seeds: tuple[int, ...] = (0,)) -> dict:
    """Run the study to `cut` on the full data, on data truncated at the cut's
    close, and on data scrambled after the cut. Returns what differed."""
    t = P.at(cut, P.CLOSE)
    base = decisions(make_study, pit, start, cut, sim_kw)
    trunc = decisions(make_study, pit.truncated(t), start, cut, sim_kw)
    out = {"cut": cut, "decisions": len(base), "truncation_ok": trunc == base, "scramble_ok": True}
    for s in seeds:
        if decisions(make_study, pit.scrambled(t, seed=s), start, cut, sim_kw) != base:
            out["scramble_ok"] = False
            break
    out["ok"] = out["truncation_ok"] and out["scramble_ok"]
    return out


def check(make_study: Callable, pit: P.PIT, start: str, cuts: str | list[str], sim_kw: dict | None = None,
          seeds: tuple[int, ...] = (0,)) -> dict:
    """check_one at each cut. A leak shows only on days the study acts, so
    test several cuts (see default_cuts)."""
    cuts = [cuts] if isinstance(cuts, str) else list(cuts)
    per = [check_one(make_study, pit, start, c, sim_kw, seeds) for c in cuts]
    return {"ok": all(r["ok"] for r in per), "truncation_ok": all(r["truncation_ok"] for r in per),
            "scramble_ok": all(r["scramble_ok"] for r in per), "cuts": per,
            "decisions": sum(r["decisions"] for r in per),
            "lookahead_datasets": {d.name: d.lookahead for d in pit.ds.values() if d.lookahead},
            "untested_datasets": [d.name for d in pit.ds.values()
                                  if d.lookahead is None and not (d.event_time and d.value_cols)]}


def default_cuts(cal: P.Calendar, start: str, end: str, n: int = 6) -> list[str]:
    """n cut days spread evenly over the run."""
    days = cal.range(start, end)
    return [days[int(i)] for i in np.linspace(len(days) // (n + 1), len(days) - 1, n)]
