#!/bin/zsh
# Trial 57 pipeline. Each model fit trains in its own fresh process (the GPU
# could not hold two fits in one process on this 8GB laptop). Each step refuses
# to run if the gate before it failed. Safe to re-run: finished models are kept.
cd /Users/masontrinkner/ClaudeCode/trading-bot
PY=/opt/anaconda3/bin/python3
caffeinate -i -w $$ &
$PY -u python/cbm_crsp.py train crsp_fit2004 200412 >> reports/cbm_crsp_gate2.log 2>&1 || exit 1
$PY -u python/cbm_crsp.py train crsp_fit2014 201412 >> reports/cbm_crsp_gate2.log 2>&1 || exit 1
$PY -u python/cbm_crsp.py gate2 >> reports/cbm_crsp_gate2.log 2>&1 || exit 1
grep -q "GATE 2 PASSED" reports/cbm_crsp_gate2.log || exit 0
$PY -u python/cbm_crsp.py costs > reports/cbm_crsp_costs.log 2>&1
$PY -u python/cbm_crsp.py train crsp_fit2022 202212 > reports/cbm_crsp_final.log 2>&1 || exit 1
$PY -u python/cbm_crsp.py test  > reports/cbm_crsp_test.log 2>&1
echo done > reports/cbm_crsp_chain.done
