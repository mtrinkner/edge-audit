#!/usr/bin/env python3
"""Prove the search controls actually control the search.

    python3 tests/test_search_controls.py

The registry, the deflated Sharpe and the lockbox only matter if they resist the
things a motivated person would do when a result disappoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import deflated_sharpe as ds  # noqa: E402

results = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("SEARCH CONTROLS")

    # The bar must rise with trials. If it did not, the correction would do nothing.
    bars = [ds.expected_max_sharpe(n, 348) for n in (1, 10, 50, 100, 500)]
    check("the bar rises with trial count", all(a < b for a, b in zip(bars, bars[1:])),
          " < ".join(f"{b:.2f}" for b in bars))

    # A single trial should impose essentially no penalty.
    check("one trial imposes no penalty", abs(bars[0]) < 1e-9, f"{bars[0]:.4f}")

    # More data must make a given Sharpe more believable, not less.
    small = ds.deflated_sharpe_ratio(1.0, 50, 100)
    large = ds.deflated_sharpe_ratio(1.0, 50, 1000)
    check("more observations raise confidence", large > small,
          f"{small:.3f} at n=100 -> {large:.3f} at n=1000")

    # More searching must make the same Sharpe less believable.
    few = ds.deflated_sharpe_ratio(1.0, 5, 348)
    many = ds.deflated_sharpe_ratio(1.0, 500, 348)
    check("more trials lower confidence", many < few,
          f"{few:.3f} at 5 trials -> {many:.3f} at 500")

    # The project's own result must fail its own test.
    bar = ds.expected_max_sharpe(49, 348)
    check("this project's best sweep fails the bar", 0.83 < bar,
          f"observed 0.830 vs bar {bar:.3f}")

    # A genuinely strong result on a small search should pass.
    strong = ds.deflated_sharpe_ratio(2.5, 3, 500)
    check("a strong result on few trials passes", strong > 0.95, f"{strong:.3f}")

    # The lockbox must refuse a query that reaches past the seal.
    import lockbox
    st = lockbox.state()
    if st["sealed"] and not st["opened"]:
        seal = st["seal_from"]
        later = f"{int(seal[:4]) + 1}{seal[4:]}"
        try:
            lockbox.guard(later, "test")
            check("lockbox blocks a query past the seal", False, "no exception raised")
        except lockbox.LockboxViolation:
            check("lockbox blocks a query past the seal", True, f"refused {later}")
        try:
            lockbox.guard("2020-01-01", "test")
            check("lockbox allows earlier data", True)
        except lockbox.LockboxViolation:
            check("lockbox allows earlier data", False, "blocked data it should allow")
    else:
        check("lockbox sealed", False, "seal it with python3 python/lockbox.py seal")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
