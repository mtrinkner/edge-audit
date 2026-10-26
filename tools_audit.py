"""Static audit of the research code for known failure modes. Evidence only."""
import re, subprocess
from pathlib import Path
R = Path(".")
PY = sorted(p for p in (R/"python").glob("*.py"))
def grep(pat, files=PY, flags=0):
    out=[]
    for f in files:
        for i,l in enumerate(f.read_text().splitlines(),1):
            if re.search(pat,l,flags): out.append((f.name,i,l.strip()[:95]))
    return out

FINDINGS=[]
def add(sev,cat,title,ev,direction,fix):
    FINDINGS.append(dict(sev=sev,cat=cat,title=title,ev=ev,direction=direction,fix=fix))

# 1. session-wide transforms = lookahead shape
ev=grep(r'groupby\(\[.*session.*\]\)\[.{0,20}\]\.transform\(["\'](mean|sum|last|max|min|std)')
ev+=grep(r'\.transform\(["\']last["\']\)')
if ev: add("HIGH","lookahead","session-wide transform used as a feature input",ev,
    "inflates: selects bars whose future was quiet","use expanding()/shift(1); covered by test_no_lookahead_intraday")

# 2. same-bar entry (decide and fill on the same close)
ev=grep(r'entry\s*=\s*.*close\[|buy.*at.*close\b.*signal|close\.iloc\[i\].*entry')
if ev: add("MED","execution","entry priced at the signal bar's own close",ev,
    "inflates: assumes a fill at a price you only knew at the bar's end","enter at next bar open")

# 3. annualization of overlapping returns
ev=grep(r'np\.sqrt\(252')
nonov=grep(r'\[::\s*h\s*\]|\[::\s*HOLD\s*\]|\[::\s*med_hold')
add("INFO","statistics",f"sqrt(252) annualization sites: {len(ev)}; non-overlap thinning sites: {len(nonov)}",
    ev[:6]+nonov[:6],"inflates t-stats if thinning is missing anywhere","every Sharpe must thin by the label horizon")

# 4. short selling without borrow cost
ev=grep(r'short|orientation.*-1',PY)
borrow=grep(r'borrow|rebate|hard_to_borrow|locate')
if ev and not borrow: add("MED","costs","strategies take short positions; no borrow-cost model anywhere",
    [("(absence)",0,f"{len(ev)} short-related lines, 0 borrow-cost lines")],
    "inflates: shorting is assumed free","add a borrow-cost term or restrict to long-only")

# 5. universe survivorship
ev=grep(r'survivorship',[R/"sql"/"01_schema.sql"] if (R/"sql"/"01_schema.sql").exists() else [])
add("CRITICAL","survivorship","universe is current index membership",
    ev[:3] or [("symbols table",0,"inclusion_reason = '... as of 2026-10 (survivorship bias)'")],
    "inflates: delisted/acquired/dropped names absent","needs point-in-time membership; see Phase 2")

# 6. estimated spread reported as a cost
ev=grep(r'edge_full|liquidity_half_spread_bps')
add("MED","costs","all execution costs are ESTIMATED; no observed quote anywhere",
    ev[:5],"unknown sign: EDGE overstates mega-cap spreads, old heuristic understated stress",
    "label estimated vs observed; run cost scenarios A-D")

# 7. data/code disagreement after the TA narrowing
import sqlite3
con=sqlite3.connect("data/warehouse.db")
tabs=[r[0] for r in con.execute("select name from sqlite_master where type='table'")]
orphan=[t for t in ("earnings","fundamentals","insider_trades","short_interest") if t in tabs]
ing=[p.name for p in PY if p.name.startswith("ingest_")]
if orphan: add("MED","reproducibility","warehouse holds tables whose ingest code was deleted",
    [("warehouse.db",0,f"tables present: {orphan}"),("python/",0,f"ingest modules present: {ing}")],
    "none, but results cannot be rebuilt from source","restore ingest or drop the tables")

print(f"{'SEV':<9}{'CATEGORY':<14}FINDING")
print("="*100)
for f in sorted(FINDINGS,key=lambda x:["CRITICAL","HIGH","MED","INFO"].index(x["sev"])):
    print(f"{f['sev']:<9}{f['cat']:<14}{f['title']}")
    for n,i,l in f["ev"][:3]:
        print(f"         evidence: {n}:{i}  {l}")
    print(f"         bias: {f['direction']}")
    print(f"         fix:  {f['fix']}\n")
