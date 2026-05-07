from __future__ import annotations

import json
from pathlib import Path

from execution.live_bridge import LiveOrderAuditStore, LiveOrderBridge
from execution.paper_bridge import PaperTradeBridge
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, FileCandidateProvider
from services.decision_engine import DecisionEngine
from services.execution_guard.precheck import SubmitPrecheck


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
    checker = SubmitPrecheck()
    store = LiveOrderAuditStore(repo / 'state' / 'runs')

    rows = []
    for req in requests[:3]:
        result = checker.evaluate(req, mode='paper')
        path = store.save(req, 'blocked' if not result.allowed else 'ready', result.reasons)
        rows.append({'symbol': req.symbol, 'allowed': result.allowed, 'reasons': result.reasons, 'audit': path.name})

    out = repo / 'state' / 'runs' / 'live_precheck_demo.json'
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == '__main__':
    main()
