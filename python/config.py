"""Central configuration for the research pipeline.

Every magic number the pipeline depends on lives here, so a reader can audit the
assumptions in one place and a reviewer can change them without hunting through
modules. Nothing downstream hardcodes a cost, a date, or a path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
DB_PATH = DATA / "warehouse.db"
JOURNAL_XLSX = DATA / "journal.xlsx"
DASHBOARD_XLSX = ROOT / "reports" / "dashboard.xlsx"
SQL_DIR = ROOT / "sql"
UNIVERSE_CSV = DATA / "universe.csv"
FIGURES = ROOT / "reports" / "figures"

# ---------------------------------------------------------------- sample window
# Start deliberately before the 2020 crash and the 2022 bear so the sample
# contains more than one regime. A strategy tested only on 2023-2025 has seen
# one upward-trending market and proves nothing.
START_DATE = date(2014, 1, 1)
END_DATE = date(2025, 12, 31)

# ------------------------------------------------------------- execution costs
@dataclass(frozen=True)
class CostModel:
    """Costs are modeled, not ignored. This is where most backtests lie.

    commission_per_share: retail equity commissions are near zero, but routing
        and regulatory fees are not. Kept as a parameter so sensitivity to it
        can be tested rather than assumed away.
    slippage_bps: applied to every fill, adverse to the trade direction. A
        market order does not fill at the close that generated the signal.
    spread_bps: half-spread paid on entry and exit.
    """

    commission_per_share: float = 0.005
    min_commission: float = 1.00
    slippage_bps: float = 5.0
    spread_bps: float = 2.0
    borrow_bps_annual: float = 50.0  # cost of holding a short

    def round_trip_bps(self) -> float:
        return 2 * (self.slippage_bps + self.spread_bps)


COSTS = CostModel()

# ------------------------------------------------------------------ label setup
# Forward horizon for the supervised label. 5 trading days keeps the project
# honest about what it is: a short-horizon directional study, not day trading.
# Intraday claims require intraday data, which this pipeline does not have.
LABEL_HORIZON_DAYS = 5
LABEL_THRESHOLD = 0.0  # a "win" is forward return above this, after costs

# --------------------------------------------------------- validation protocol
# Walk-forward, not random k-fold. Random folds leak the future into training
# on time series data and inflate every metric.
WF_TRAIN_YEARS = 4
WF_TEST_YEARS = 1
WF_STEP_YEARS = 1
# Purge gap between train and test, in trading days. Must be at least the label
# horizon or the last training labels overlap the test window.
PURGE_DAYS = LABEL_HORIZON_DAYS
EMBARGO_DAYS = 5

# ----------------------------------------------------------------- risk limits
# Mirrors journal/risk-profile.json so the backtest sizes positions the same way
# the paper journal does. Comparable numbers matter more than optimal ones.
ACCOUNT_SIZE = 10_000.0
MAX_RISK_PCT_PER_TRADE = 1.0
MAX_EXPOSURE_PCT = 50.0
MAX_OPEN_POSITIONS = 3

RANDOM_SEED = 20260101


@dataclass(frozen=True)
class IngestConfig:
    source: str = "yfinance"
    interval: str = "1d"
    batch_size: int = 25
    max_retries: int = 3
    auto_adjust: bool = False  # keep raw and adjusted columns both


INGEST = IngestConfig()


def ensure_dirs() -> None:
    for p in (DATA, RAW, FIGURES, DASHBOARD_XLSX.parent):
        p.mkdir(parents=True, exist_ok=True)
