from __future__ import annotations

from typing import Any


def build_alerts(report: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if not report.get("opend_reachable"):
        alerts.append({"level": "high", "message": "Futu OpenD unreachable"})
    if not report.get("sdk_available"):
        alerts.append({"level": "medium", "message": "Futu SDK unavailable"})
    if report.get("readonly_account_status") != "connected":
        alerts.append({"level": "high", "message": f"account not connected: {report.get('readonly_account_status')}"})
    reconciliation = report.get("reconciliation", {}) or {}
    if reconciliation and not reconciliation.get("allowed", True):
        alerts.append({"level": "high", "message": "reconciliation blocked execution"})
    if int(report.get("order_state_failed_count", 0) or 0) > 0:
        alerts.append({"level": "medium", "message": "failed order states exist"})
    if report.get("live_submit_enabled"):
        alerts.append({"level": "critical", "message": "live submit must remain disabled"})
    return alerts
