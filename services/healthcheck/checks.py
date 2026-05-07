from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider, FutuSdkClient
from services.futu_opend import OpenDClient
from services.trade_state import OrderStateStore

from .alerts import build_alerts


class HealthcheckService:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root)
        self.runs = self.repo_root / "state" / "runs"

    def run(self) -> dict[str, Any]:
        opend = OpenDClient().probe()
        sdk = FutuSdkClient().availability()
        account = FutuAccountProvider().get_summary()
        reconciliation_path = self.runs / "futu_sim_position_reconcile.json"
        reconciliation = ReconciliationGuard(reconciliation_path, fail_closed=False).evaluate()
        order_summary = OrderStateStore(self.runs / "orders").summary()
        report = {
            "python_runtime": sys.version.split()[0],
            "opend_reachable": opend.reachable,
            "opend_message": opend.message,
            "sdk_available": sdk.available,
            "sdk_message": sdk.message,
            "readonly_account_status": account.status,
            "readonly_account_message": account.message,
            "readonly_account_env": account.env,
            "readonly_position_count": len(account.positions),
            "readonly_order_count": len(account.orders),
            "reconciliation": {
                "allowed": reconciliation.allowed,
                "reasons": reconciliation.reasons,
                "blocking_level": reconciliation.blocking_level,
                "diff_symbols": reconciliation.diff_symbols,
            },
            "order_state_total_count": order_summary.get("total", 0),
            "order_state_open_count": order_summary.get("open", 0),
            "order_state_failed_count": order_summary.get("failed", 0),
            "paper_submit_enabled": True,
            "sim_submit_enabled": False,
            "live_submit_enabled": False,
        }
        report["alerts"] = build_alerts(report)
        report["status"] = "blocked" if any(a["level"] in {"high", "critical"} for a in report["alerts"]) else "ok"
        return report
