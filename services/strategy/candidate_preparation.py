from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from vnpy_llm.base import beijing_now_isoformat

from .candidate_generation import HybridCandidateGenerationService

REQUIRED_ROW_FIELDS = (
    "symbol",
    "market",
    "name",
    "candidate_type",
    "raw_score",
    "rationale",
    "risk",
    "action_hint",
    "signals",
    "selection_policy",
    "strategy_tags",
    "source_breakdown",
)
RECOMMENDED_ROW_FIELDS = (
    "confidence_source",
    "generated_at",
    "as_of_date",
    "theme_bucket",
    "risk_level",
    "consensus_score",
    "explanation_ready",
    "explanation_summary",
    "research_note",
    "data_completeness",
    "max_signal_score",
    "scoring",
)


class CandidateInputPreparationService:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.runs_root = self.repo_root / "state" / "runs"
        self.dynamic_path = self.runs_root / "candidate_inputs.dynamic.json"
        self.static_path = self.runs_root / "candidate_inputs.json"
        self.report_path = self.runs_root / "candidate_inputs.prepare.report.json"
        self.generation_service = HybridCandidateGenerationService(self.repo_root)

    def prepare(
        self,
        *,
        dynamic_sources: Iterable[str | Path] | None = None,
        static_sources: Iterable[str | Path] | None = None,
        generated_at: str | None = None,
        as_of_date: str | None = None,
        include_market_data: bool = False,
        knot_runtime: str = "auto",
    ) -> dict[str, Any]:
        generated_at = generated_at or beijing_now_isoformat()
        as_of_date = as_of_date or generated_at.split("T", 1)[0]
        self.runs_root.mkdir(parents=True, exist_ok=True)

        resolved_dynamic_sources = self._resolve_sources(dynamic_sources, fallback=self.dynamic_path)
        resolved_static_sources = self._resolve_sources(static_sources, fallback=self.static_path)

        dynamic_result = self._prepare_target(
            target_path=self.dynamic_path,
            mode="dynamic",
            sources=resolved_dynamic_sources,
            generated_at=generated_at,
            as_of_date=as_of_date,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        static_result = self._prepare_target(
            target_path=self.static_path,
            mode="static",
            sources=resolved_static_sources,
            generated_at=generated_at,
            as_of_date=as_of_date,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )

        report = {
            "schema_version": "candidate_prepare_report_v2",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "report_path": str(self.report_path),
            "targets": {
                "dynamic": dynamic_result,
                "static": static_result,
            },
            "summary": {
                "written_targets": [
                    name
                    for name, item in (("dynamic", dynamic_result), ("static", static_result))
                    if item.get("written")
                ],
                "market_coverage": sorted(
                    set(dynamic_result.get("market_coverage", [])) | set(static_result.get("market_coverage", []))
                ),
                "total_items": int(dynamic_result.get("item_count", 0) or 0) + int(static_result.get("item_count", 0) or 0),
                "include_market_data": bool(include_market_data),
                "knot_runtime": str(knot_runtime or "auto"),
                "warnings": list(
                    dict.fromkeys(
                        [
                            *dynamic_result.get("warnings", []),
                            *static_result.get("warnings", []),
                        ]
                    )
                ),
                "missing_required_field_counts": self._merge_counters(
                    dynamic_result.get("missing_required_field_counts", {}),
                    static_result.get("missing_required_field_counts", {}),
                ),
            },
        }
        sanitized_report = self._sanitize_json_data(report)
        self.report_path.write_text(
            json.dumps(sanitized_report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return sanitized_report

    def _prepare_target(
        self,
        *,
        target_path: Path,
        mode: str,
        sources: list[Path],
        generated_at: str,
        as_of_date: str,
        include_market_data: bool,
        knot_runtime: str,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        if not sources:
            warnings.append(f"No source file was provided or found for {target_path.name}; skipped preparation.")
            return {
                "path": str(target_path),
                "written": False,
                "mode": mode,
                "selection_policy": self.generation_service.scoring_service.selection_policy_for(mode),
                "source_files": [],
                "item_count": 0,
                "market_coverage": [],
                "warnings": warnings,
                "missing_required_field_counts": {},
            }

        payload = self.generation_service.generate(
            mode=mode,
            sources=sources,
            generated_at=generated_at,
            as_of_date=as_of_date,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        payload["row_requirements"] = {
            "required_fields": list(REQUIRED_ROW_FIELDS),
            "recommended_fields": list(RECOMMENDED_ROW_FIELDS),
        }
        payload["preparation_metadata"] = {
            "prepared_by": self.__class__.__name__,
            "prepared_mode": mode,
            "report_path": str(self.report_path),
            "include_market_data": bool(include_market_data),
            "knot_runtime": str(knot_runtime or "auto"),
        }

        missing_fields = Counter()
        for row in payload.get("items", []):
            for field in REQUIRED_ROW_FIELDS:
                if not self._field_present(row, field):
                    missing_fields[field] += 1
        if missing_fields:
            warnings.append(f"Prepared {target_path.name} with rows that still miss some required fields; see missing_required_field_counts.")
        warnings.extend(payload.get("warnings", []))

        sanitized_payload = self._sanitize_json_data(payload)
        target_path.write_text(
            json.dumps(sanitized_payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return {
            "path": str(target_path),
            "written": True,
            "mode": payload.get("mode", mode),
            "selection_policy": payload.get("selection_policy"),
            "scoring_model": payload.get("scoring_model", {}),
            "enrichment": payload.get("enrichment", {}),
            "source_files": [str(path) for path in sources],
            "item_count": payload.get("item_count", 0),
            "market_coverage": payload.get("market_coverage", []),
            "market_counts": payload.get("market_counts", {}),
            "warnings": list(dict.fromkeys(warnings)),
            "missing_required_field_counts": dict(missing_fields),
        }

    def _resolve_sources(self, sources: Iterable[str | Path] | None, *, fallback: Path) -> list[Path]:
        if sources is None:
            return [fallback] if fallback.exists() else []
        resolved = [Path(item) for item in sources if str(item).strip()]
        return [path for path in resolved if path.exists()]

    def _field_present(self, row: dict[str, Any], field: str) -> bool:
        value = row.get(field)
        if field == "signals":
            return isinstance(value, list) and bool(value)
        if field in {"strategy_tags"}:
            return isinstance(value, list) and bool(value)
        if field in {"source_breakdown"}:
            return isinstance(value, dict) and bool(value)
        return value not in {None, ""}

    def _merge_counters(self, left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        merged = Counter(left or {})
        merged.update(right or {})
        return dict(merged)

    def _sanitize_json_data(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: self._sanitize_json_data(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_json_data(item) for item in data]
        elif isinstance(data, (float, int)):
            if math.isnan(data) or math.isinf(data):
                return None
            return data
        return data
