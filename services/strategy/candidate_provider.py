from __future__ import annotations

import json
from pathlib import Path


class UnifiedCandidateProvider:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def load(self) -> list[dict]:
        dynamic_path = self.repo_root / 'state' / 'runs' / 'candidate_inputs.dynamic.json'
        static_path = self.repo_root / 'state' / 'runs' / 'candidate_inputs.json'
        if dynamic_path.exists():
            data = json.loads(dynamic_path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and isinstance(data.get('items'), list) and data['items']:
                return data['items']
        if static_path.exists():
            return json.loads(static_path.read_text(encoding='utf-8'))
        return []
