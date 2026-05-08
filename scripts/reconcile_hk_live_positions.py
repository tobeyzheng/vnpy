from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.futu_account import FutuSdkClient
from services.trade_state import OrderStateStore


def to_hk_symbol(code: str) -> str:
    text = str(code or "").upper()
    if text.startswith("HK."):
        return f"{text.replace('HK.', '', 1).zfill(5)}.HK"
    if text.endswith(".HK"):
        left = text.split(".", 1)[0]
        return f"{left.zfill(5)}.HK"
    if text.isdigit():
        return f"{text.zfill(5)}.HK"
    return text


def broker_positions(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("positions", []) or []:
        symbol = to_hk_symbol(str(row.get("code", "")))
        if not symbol.endswith(".HK"):
            continue
        qty = int(float(row.get("qty", 0) or 0))
        can_sell_qty = int(float(row.get("can_sell_qty", qty) or 0))
        if qty == 0 and can_sell_qty == 0:
            continue
        result[symbol] = {
            "qty": qty,
            "can_sell_qty": can_sell_qty,
            "cost_price": float(row.get("cost_price", 0) or 0),
            "nominal_price": float(row.get("nominal_price", 0) or 0),
        }
    return result


def local_positions(order_store: OrderStateStore) -> dict[str, int]:
    qty_by_symbol: dict[str, int] = defaultdict(int)
    for state in order_store.list():
        if state.market != "hong_kong":
            continue
        if state.status not in {"filled", "reconciled"}:
            continue
        qty = int(state.filled_qty or state.qty or 0)
        symbol = to_hk_symbol(state.symbol)
        if state.side == "BUY":
            qty_by_symbol[symbol] += qty
        elif state.side == "SELL":
            qty_by_symbol[symbol] -= qty
    return {symbol: max(qty, 0) for symbol, qty in qty_by_symbol.items() if qty > 0}


def main() -> None:
    runs = REPO_ROOT / "state" / "runs"
    client = FutuSdkClient()
    avail = client.availability()
    out = {"success": False, "message": avail.message, "env": None, "diffs": []}
    if avail.available:
        try:
            snapshot = client.account_snapshot()
            message = str(snapshot.get("message", ""))
            broker = broker_positions(snapshot)
            local = local_positions(OrderStateStore(runs / "orders"))
            symbols = sorted(set(broker) | set(local))
            diffs = []
            for symbol in symbols:
                broker_qty = int(broker.get(symbol, {}).get("qty", 0) or 0)
                broker_can_sell = int(broker.get(symbol, {}).get("can_sell_qty", broker_qty) or 0)
                local_qty = int(local.get(symbol, 0) or 0)
                diffs.append({
                    "symbol": symbol,
                    "local_qty": local_qty,
                    "futu_qty": broker_qty,
                    "futu_can_sell_qty": broker_can_sell,
                    "qty_match": local_qty == broker_qty,
                    "sellable_match": local_qty <= broker_can_sell if broker_can_sell else local_qty == 0,
                    "local_avg_price": None,
                    "futu_cost_price": broker.get(symbol, {}).get("cost_price"),
                    "futu_nominal_price": broker.get(symbol, {}).get("nominal_price"),
                })
            out = {"success": True, "message": message or "ok", "env": "REAL" if "env=REAL" in message else "UNKNOWN", "diffs": diffs}
        except Exception as exc:
            out = {"success": False, "message": str(exc), "env": None, "diffs": []}
    path = runs / "futu_live_position_reconcile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
