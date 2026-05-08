from __future__ import annotations

"""Walk-forward aggregator for NVDA 1m refined grid.

Reads W1/W2/W3 grid summaries produced by run_vnpy_cta_nvda_grid.py and
computes per-parameter-combo cross-window statistics:
  - mean / min / std of return_drawdown_ratio
  - mean total_return, mean sharpe_ratio, mean max_ddpercent
  - stability_score = mean_rdd / (1 + std_rdd)   (higher is more stable)
  - coverage = number of windows with status=ok
"""

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT_DIR = REPO_ROOT / "state" / "runs" / "classic_multifactor"
WINDOWS = ["W1", "W2", "W3"]
WF_SUMMARY_PATH = OUTPUT_DIR / "vnpy_cta_nvda_walk_forward.json"


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except Exception:
        return None


def load_window_summary(window_tag: str) -> dict[str, Any] | None:
    path = OUTPUT_DIR / f"vnpy_cta_nvda_grid_{window_tag}_summary.json"
    if not path.exists():
        print(f"[warn ] {path} not found, skipping {window_tag}")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_combo_key(row: dict[str, Any]) -> str:
    return f"si={row['signal_interval_minutes']}_es={row['entry_score']}"


def aggregate() -> dict[str, Any]:
    # Load all available windows
    window_data: dict[str, list[dict[str, Any]]] = {}
    for wt in WINDOWS:
        summary = load_window_summary(wt)
        if summary is None:
            continue
        rows = summary.get("rows_raw", [])
        window_data[wt] = rows

    if not window_data:
        print("[error] no window summaries found")
        return {}

    # Collect all combo keys from first available window
    first_rows = next(iter(window_data.values()))
    combo_keys = [build_combo_key(r) for r in first_rows]

    # For each combo, gather metrics across windows
    combo_stats: list[dict[str, Any]] = []
    for row0 in first_rows:
        key = build_combo_key(row0)
        si = row0["signal_interval_minutes"]
        es = row0["entry_score"]
        tag = row0["tag"]

        rdds: list[float] = []
        returns: list[float] = []
        sharpes: list[float] = []
        dds: list[float] = []
        trades_list: list[int] = []
        win_rates: list[float] = []
        pl_ratios: list[float] = []
        hold_mins: list[float] = []
        coverage = 0
        per_window: dict[str, Any] = {}

        for wt, rows in window_data.items():
            # Find matching row by combo key
            match = next(
                (r for r in rows if build_combo_key(r) == key), None
            )
            if match is None or match.get("status") != "ok":
                per_window[wt] = {"status": match.get("status") if match else "missing"}
                continue
            coverage += 1
            rdd = _safe_float(match.get("return_drawdown_ratio"))
            ret = _safe_float(match.get("total_return"))
            sh = _safe_float(match.get("sharpe_ratio"))
            dd = _safe_float(match.get("max_ddpercent"))
            tc = match.get("total_trade_count") or match.get("trade_count_runtime") or 0
            wr = _safe_float(match.get("win_rate_pair"))
            pl = _safe_float(match.get("profit_loss_ratio_pair"))
            hm = _safe_float(match.get("avg_holding_minutes"))

            if rdd is not None:
                rdds.append(rdd)
            if ret is not None:
                returns.append(ret)
            if sh is not None:
                sharpes.append(sh)
            if dd is not None:
                dds.append(dd)
            try:
                trades_list.append(int(tc))
            except Exception:
                pass
            if wr is not None:
                win_rates.append(wr)
            if pl is not None:
                pl_ratios.append(pl)
            if hm is not None:
                hold_mins.append(hm)

            per_window[wt] = {
                "status": "ok",
                "return_drawdown_ratio": rdd,
                "total_return": ret,
                "sharpe_ratio": sh,
                "max_ddpercent": dd,
                "total_trade_count": tc,
                "win_rate_pair": wr,
                "profit_loss_ratio_pair": pl,
                "avg_holding_minutes": hm,
            }

        def _mean(xs: list[float]) -> float | None:
            return round(sum(xs) / len(xs), 6) if xs else None

        def _std(xs: list[float]) -> float | None:
            if len(xs) < 2:
                return None
            m = sum(xs) / len(xs)
            return round((sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5, 6)

        mean_rdd = _mean(rdds)
        std_rdd = _std(rdds)
        min_rdd = round(min(rdds), 6) if rdds else None
        stability_score: float | None = None
        if mean_rdd is not None:
            denom = 1.0 + (std_rdd or 0.0)
            stability_score = round(mean_rdd / denom, 6)

        combo_stats.append(
            {
                "tag": tag,
                "signal_interval_minutes": si,
                "entry_score": es,
                "coverage": coverage,
                "mean_rdd": mean_rdd,
                "min_rdd": min_rdd,
                "std_rdd": std_rdd,
                "stability_score": stability_score,
                "mean_total_return": _mean(returns),
                "mean_sharpe": _mean(sharpes),
                "mean_max_ddpercent": _mean(dds),
                "mean_trades": _mean([float(t) for t in trades_list]),
                "mean_win_rate_pair": _mean(win_rates),
                "mean_pl_ratio_pair": _mean(pl_ratios),
                "mean_holding_minutes": _mean(hold_mins),
                "per_window": per_window,
            }
        )

    # Sort by stability_score desc (None last)
    def _rank_key(r: dict[str, Any]) -> float:
        v = r.get("stability_score")
        try:
            f = float(v)
            if f != f:
                return float("-inf")
            return f
        except Exception:
            return float("-inf")

    ranked = sorted(combo_stats, key=_rank_key, reverse=True)

    result = {
        "symbol": "NVDA.US",
        "interval": "1m",
        "windows_available": list(window_data.keys()),
        "windows_requested": WINDOWS,
        "rank_key": "stability_score (= mean_rdd / (1 + std_rdd))",
        "combos_total": len(combo_stats),
        "ranked": ranked,
    }
    return result


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def build_markdown(ranked: list[dict[str, Any]], windows: list[str]) -> str:
    w_cols = " | ".join(f"rdd_{w}" for w in windows)
    header = f"| tag | si | es | cov | mean_rdd | min_rdd | std_rdd | stability | {w_cols} | mean_ret | mean_sh |"
    sep = "|" + "|".join(["---"] * (10 + len(windows))) + "|"
    lines = [header, sep]
    for r in ranked:
        pw = r.get("per_window", {})
        w_vals = " | ".join(_fmt(pw.get(w, {}).get("return_drawdown_ratio")) for w in windows)
        lines.append(
            "| {tag} | {si} | {es} | {cov} | {mr} | {mnr} | {sr} | {ss} | {wv} | {mret} | {msh} |".format(
                tag=r["tag"],
                si=r["signal_interval_minutes"],
                es=r["entry_score"],
                cov=r["coverage"],
                mr=_fmt(r.get("mean_rdd")),
                mnr=_fmt(r.get("min_rdd")),
                sr=_fmt(r.get("std_rdd")),
                ss=_fmt(r.get("stability_score")),
                wv=w_vals,
                mret=_fmt(r.get("mean_total_return")),
                msh=_fmt(r.get("mean_sharpe")),
            )
        )
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = aggregate()
    if not result:
        sys.exit(1)

    WF_SUMMARY_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    windows_available = result.get("windows_available", [])
    ranked = result.get("ranked", [])
    print(f"\n=== Walk-Forward Ranking (stability_score desc) ===")
    print(f"Windows available: {windows_available}")
    print(build_markdown(ranked, windows_available))
    print(f"\n[summary] {WF_SUMMARY_PATH}")
    print(f"Top-3 combos:")
    for r in ranked[:3]:
        print(
            f"  {r['tag']} si={r['signal_interval_minutes']} es={r['entry_score']} "
            f"stability={_fmt(r.get('stability_score'))} mean_rdd={_fmt(r.get('mean_rdd'))} "
            f"coverage={r['coverage']}"
        )


if __name__ == "__main__":
    main()
