from __future__ import annotations

import json
from pathlib import Path

from execution.live_bridge import LiveOrderBridge
from execution.paper_bridge import PaperTradeBridge
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, FileCandidateProvider
from services.common.config_loader import load_yaml
from services.decision_engine import DecisionEngine
from services.execution_guard.live_gate import LiveExecutionGate
from services.execution_guard.precheck import SubmitPrecheck
from services.risk_engine import LiveRiskGuard
from services.trade_state import ApprovalRecord, ApprovalStateStore, TradeStateRecord, TradeStateStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    provider = CompositeCandidateProvider([FileCandidateProvider(repo / 'state' / 'runs' / 'candidate_inputs.json')])
    inputs = provider.get_candidate_inputs('hong_kong')
    candidates = [candidate_from_input(i) for i in inputs]
    for c in candidates:
        c.confidence = c.confidence or 75
    decision = DecisionEngine().decide('hong_kong', candidates[:3])
    intents = PaperTradeBridge().build_intents(decision)
    requests = LiveOrderBridge().build_requests(intents)
    if not requests:
        print('no requests')
        return

    req = requests[0]
    approval_store = ApprovalStateStore(repo / 'state' / 'runs')
    trade_store = TradeStateStore(repo / 'state' / 'runs')

    approval = ApprovalRecord(request_id=req.request_id, status='pending', approver=None, reason='waiting for manual approval')
    approval_path = approval_store.save(approval)

    limits = load_yaml(repo / 'configs' / 'risk' / 'live_risk_limits.yaml')['limits']
    gate = LiveExecutionGate(SubmitPrecheck(), LiveRiskGuard(limits))
    gate_result = gate.evaluate(
        req,
        mode='paper',
        signal_age_seconds=60,
        market_existing_pct=0.10,
        daily_new_pct=0.05,
        current_drawdown_pct=0.01,
        account_status='connected',
        approval_status=approval.status,
    )

    phase = 'blocked_before_live' if not gate_result.allowed else 'ready_to_submit'
    trade_state = TradeStateRecord(request_id=req.request_id, symbol=req.symbol, market=req.market, phase=phase, notes=gate_result.reasons)
    trade_path = trade_store.save(trade_state)

    out = repo / 'state' / 'runs' / 'trade_state_flow_demo.json'
    out.write_text(json.dumps({
        'approval': approval_path.name,
        'trade_state': trade_path.name,
        'allowed': gate_result.allowed,
        'reasons': gate_result.reasons,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps({
        'approval': approval_path.name,
        'trade_state': trade_path.name,
        'allowed': gate_result.allowed,
        'reasons': gate_result.reasons,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
