"""Summarize the TSLA 1m sweep results under this folder.

Reads every R??_es*_ts*_ap*.json produced by run_vnpy_cta_backtest.py and
prints (a) a full table and (b) Top-3 by sharpe / total_return, filtered
to runs with trades >= 10 to avoid statistically-meaningless outliers.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATTERN = re.compile(r"R(\d+)_es([\d.]+)_ts([\d.]+)_ap([\d.]+)\.json")


def main() -> None:
    rows: list[dict] = []
    for fn in sorted(os.listdir(ROOT)):
        m = PATTERN.match(fn)
        if not m:
            continue
        rid, es, ts, ap = m.groups()
        with open(ROOT / fn) as f:
            raw = json.load(f)
        d = raw.get("stats", raw)  # backtest writes metrics under "stats"
        rows.append({
            "id": int(rid), "es": float(es), "ts": float(ts), "ap": float(ap),
            "trades": int(float(d.get("total_trade_count", 0) or 0)),
            "ret": float(d.get("total_return", 0) or 0),
            "ann": float(d.get("annual_return", 0) or 0),
            "sharpe": float(d.get("sharpe_ratio", 0) or 0),
            "mdd": float(d.get("max_ddpercent", 0) or 0),
            "pf_days": int(float(d.get("profit_days", 0) or 0)),
            "ls_days": int(float(d.get("loss_days", 0) or 0)),
            "end": float(d.get("end_balance", 0) or 0),
        })

    print(" id |  es  |  ts  |   ap   | trades |  ret%  |  ann%  | sharpe | pf | ls | mdd%  |  end$")
    print("----+------+------+--------+--------+--------+--------+--------+----+----+-------+-------")
    for r in rows:
        print(
            f"{r['id']:>3} | {r['es']:.2f} | {r['ts']:.2f} | {r['ap']:.4f} |"
            f" {r['trades']:>6} | {r['ret']:>6.2f} | {r['ann']:>6.2f} |"
            f" {r['sharpe']:>6.2f} |{r['pf_days']:>3} |{r['ls_days']:>3} |"
            f" {r['mdd']:>5.2f} |{r['end']:>7.0f}"
        )

    eligible = [r for r in rows if r["trades"] >= 10]
    print(f"\nEligible runs (trades>=10): {len(eligible)} / {len(rows)}")

    print("\n=== TOP-3 by sharpe ===")
    for r in sorted(eligible, key=lambda x: x["sharpe"], reverse=True)[:3]:
        print(
            f"  R{r['id']:02d}  es={r['es']} ts={r['ts']} ap={r['ap']}"
            f"  trades={r['trades']} ret={r['ret']:.2f}% sharpe={r['sharpe']:.2f}"
            f" mdd={r['mdd']:.2f}% pf/ls={r['pf_days']}/{r['ls_days']}"
        )

    print("\n=== TOP-3 by total_return ===")
    for r in sorted(eligible, key=lambda x: x["ret"], reverse=True)[:3]:
        print(
            f"  R{r['id']:02d}  es={r['es']} ts={r['ts']} ap={r['ap']}"
            f"  trades={r['trades']} ret={r['ret']:.2f}% sharpe={r['sharpe']:.2f}"
            f" mdd={r['mdd']:.2f}% pf/ls={r['pf_days']}/{r['ls_days']}"
        )


if __name__ == "__main__":
    main()
