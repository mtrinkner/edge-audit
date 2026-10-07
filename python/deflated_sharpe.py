#!/usr/bin/env python3
"""How good does a Sharpe ratio have to be, given how many you tried?

    python3 python/deflated_sharpe.py --sharpe 1.1 --trials 50 --obs 348
    python3 python/deflated_sharpe.py --table

Two numbers, both from Bailey and Lopez de Prado's work on backtest overfitting:

EXPECTED MAXIMUM SHARPE. If you test N strategies that genuinely have no edge,
the luckiest one still posts a positive Sharpe. This returns how high, so you
know what your candidate has to beat before it means anything. At 348
observations and 100 trials it is about 0.94, which is roughly what the S&P
returned over the same window. Test a hundred worthless ideas and the best one
looks like the index.

DEFLATED SHARPE RATIO. The probability that an observed Sharpe reflects real
skill rather than the best of N draws from noise. Above 0.95 is the usual bar.
It falls as trials rise and climbs as the sample grows, which is the honest
tradeoff: more searching needs more evidence.

Both assume roughly independent trials. Fifty variants of one idea are far more
correlated than fifty different ideas, so for a parameter sweep the effective N
is smaller than the raw count and these numbers are conservative.
"""

from __future__ import annotations

import argparse
import math

EULER = 0.5772156649015329


def _ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def expected_max_z(n_trials: int) -> float:
    """Expected maximum of N standard normals."""
    n = max(1, int(n_trials))
    if n == 1:
        return 0.0
    return ((1 - EULER) * _ppf(1 - 1.0 / n)
            + EULER * _ppf(1 - 1.0 / (n * math.e)))


def expected_max_sharpe(n_trials: int, n_obs: int,
                        periods_per_year: float = 252 / 5) -> float:
    """Annualized Sharpe the luckiest of N zero-edge strategies is expected to post."""
    se = 1.0 / math.sqrt(max(2, n_obs))          # SE of a per-period Sharpe near zero
    return expected_max_z(n_trials) * se * math.sqrt(periods_per_year)


def deflated_sharpe_ratio(observed_sharpe: float, n_trials: int, n_obs: int,
                          skew: float = 0.0, kurtosis: float = 3.0,
                          periods_per_year: float = 252 / 5) -> float:
    """Probability the observed Sharpe reflects skill rather than the best of N tries."""
    sr = observed_sharpe / math.sqrt(periods_per_year)      # back to per-period
    sr0 = expected_max_sharpe(n_trials, n_obs, periods_per_year) / math.sqrt(periods_per_year)
    n = max(2, n_obs)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurtosis - 1) / 4 * sr * sr))
    return _cdf((sr - sr0) * math.sqrt(n - 1) / denom)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sharpe", type=float)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--obs", type=int, default=348)
    ap.add_argument("--table", action="store_true")
    args = ap.parse_args()

    if args.table or args.sharpe is None:
        print(f"Bar a candidate must clear, by trial count ({args.obs} observations)\n")
        print(f"{'trials':>8} {'expected best by luck':>24} {'reads as':>16}")
        for n in (1, 5, 10, 25, 50, 100, 250, 500, 1000):
            b = expected_max_sharpe(n, args.obs)
            label = ("nothing" if b < 0.5 else "decent" if b < 1.0
                     else "very good" if b < 1.5 else "exceptional")
            print(f"{n:>8} {b:>24.3f} {label:>16}")
        print("\nSPY over the project's test window had Sharpe 0.91.")
        return 0

    bar = expected_max_sharpe(args.trials, args.obs)
    dsr = deflated_sharpe_ratio(args.sharpe, args.trials, args.obs)
    print(f"observed Sharpe        {args.sharpe:.3f}")
    print(f"trials                 {args.trials}")
    print(f"observations           {args.obs}")
    print(f"expected best by luck  {bar:.3f}")
    print(f"deflated Sharpe        {dsr:.3f}   (want > 0.95)")
    print()
    if dsr > 0.95 and args.sharpe > bar:
        print("Clears the bar. Worth taking to the lockbox.")
    elif args.sharpe > bar:
        print("Beats the luck threshold but the deflated probability is short of 0.95.")
        print("More observations would settle it. More trials would make it worse.")
    else:
        print("Does not clear the bar. This is what testing this many ideas produces")
        print("from pure noise. Not evidence of anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
