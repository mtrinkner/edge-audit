"""Convert Ken French's daily FF5 and momentum CSVs into data/ff/ff5_umd_daily.parquet.

Source: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
  F-F_Research_Data_5_Factors_2x3_daily_CSV.zip, F-F_Momentum_Factor_daily_CSV.zip
Output columns: date (YYYY-MM-DD), mktrf, smb, hml, rmw, cma, umd, rf, in decimals
(the library publishes percent). Load it with pit.ff_factors(path, cal).

    python python/ff_factors.py <ff5_daily.csv> <mom_daily.csv>
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "ff" / "ff5_umd_daily.parquet"


def read_block(path: str) -> pd.DataFrame:
    """The first table in a French CSV: from the header line to the first blank line."""
    lines = Path(path).read_text(errors="replace").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(","))
    body = []
    for l in lines[start:]:
        if not l.strip():
            break
        body.append(l)
    d = pd.read_csv(io.StringIO("\n".join(body)))
    d = d.rename(columns={d.columns[0]: "date"})
    d.columns = [c.strip() for c in d.columns]
    d["date"] = pd.to_datetime(d["date"].astype(str).str.strip(), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return d


def build(ff5: str, mom: str) -> pd.DataFrame:
    a = read_block(ff5).rename(columns={"Mkt-RF": "mktrf", "SMB": "smb", "HML": "hml", "RMW": "rmw",
                                        "CMA": "cma", "RF": "rf"})
    b = read_block(mom)
    b = b.rename(columns={b.columns[1]: "umd"})
    f = a.merge(b[["date", "umd"]], on="date", how="inner")
    cols = ["mktrf", "smb", "hml", "rmw", "cma", "umd", "rf"]
    f[cols] = f[cols].astype(float) / 100.0
    return f[["date"] + cols].sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    ff5, mom = sys.argv[1:3]
    f = build(ff5, mom)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(OUT, index=False)
    meta = {"source": "Ken French data library", "files": {Path(p).name: hashlib.md5(Path(p).read_bytes()).hexdigest()
                                                           for p in (ff5, mom)},
            "first_line": Path(ff5).read_text(errors="replace").splitlines()[0].strip(),
            "rows": len(f), "first": f["date"].iloc[0], "last": f["date"].iloc[-1]}
    OUT.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))
