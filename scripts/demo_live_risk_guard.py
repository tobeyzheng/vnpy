from __future__ import annotations

import json
from pathlib import Path

from execution.live_bridge import LiveOrderBridge
from execution.paper_bridge import PaperTradeBridge
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, FileCandidateProvider
from services.common.config_loader import load_yaml
from services.decision_engine import DecisionEngine
from services.risk_engine import LiveRiskGuard


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

    limits = load_yaml(repo / 'configs' / 'risk' / 'live_risk_limits.yaml')['limits']
    guard = LiveRiskGuard(limits)

    rows = []
    for req in requests:
        result = guard.evaluate(
            req,
            market_existing_pct=0.30,
            daily_new_pct=0.15,
            current_drawdown_pct=0.02,
            signal_age_seconds=120,
        )
        rows.append({'symbol': req.symbol, 'qty': req.qty, 'allowed': result.allowed, 'reasons': result.reasons})

    out = repo / 'state' / 'runs' / 'live_risk_guard_demo.json'
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == '__main__':
    main()
