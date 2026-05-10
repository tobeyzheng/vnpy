from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from vnpy_llm.base import beijing_now_isoformat

from .candidate_enrichment import CandidateKnotEnrichmentService, CandidateMarketDataService
from .candidate_scoring import CandidateScoringService, normalize_score
from .symbols import normalize_symbol

_REQUIRED_TEXT_FIELDS = ("rationale", "risk", "action_hint")


class HybridCandidateGenerationService:
    def __init__(
        self,
        repo_root: Path,
        scoring_service: CandidateScoringService | None = None,
        market_data_service: CandidateMarketDataService | None = None,
        knot_service: CandidateKnotEnrichmentService | None = None,
    ):
        self.repo_root = Path(repo_root)
        self.runs_root = self.repo_root / "state" / "runs"
        self.scoring_service = scoring_service or CandidateScoringService()
        self.market_data_service = market_data_service or CandidateMarketDataService()
        self.knot_service = knot_service or CandidateKnotEnrichmentService(self.repo_root)

    def generate(
        self,
        *,
        mode: str,
        sources: Iterable[str | Path] | None = None,
        generated_at: str | None = None,
        as_of_date: str | None = None,
        include_market_data: bool = False,
        knot_runtime: str = "auto",
    ) -> dict[str, Any]:
        mode = str(mode or "dynamic").strip().lower()
        generated_at = generated_at or beijing_now_isoformat()
        as_of_date = as_of_date or generated_at.split("T", 1)[0]
        resolved_sources = self.resolve_sources(mode=mode, sources=sources)
        rows, source_row_counts, warnings, enrichment_meta = self._collect_rows(
            mode=mode,
            sources=resolved_sources,
            generated_at=generated_at,
            as_of_date=as_of_date,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        deduped_rows = self._dedupe_rows(rows)
        sorted_rows = sorted(deduped_rows, key=self._rank_key, reverse=True)
        market_counts = Counter(str(row.get("market") or "") for row in sorted_rows if row.get("market"))
        scoring_meta = self.scoring_service.model_metadata(mode)
        return {
            "mode": f"{mode}_generated",
            "schema_version": "candidate_inputs_v3",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "selection_policy": scoring_meta["selection_policy"],
            "scoring_model": scoring_meta,
            "source_files": [str(path) for path in resolved_sources],
            "source_row_counts": dict(source_row_counts),
            "item_count": len(sorted_rows),
            "market_coverage": sorted(market_counts.keys()),
            "market_counts": dict(market_counts),
            "items": sorted_rows,
            "warnings": warnings,
            "enrichment": enrichment_meta,
        }

    def evaluate_single_candidate(
        self,
        row: Mapping[str, Any],
        *,
        mode: str,
        generated_at: str | None = None,
        as_of_date: str | None = None,
        include_market_data: bool = False,
        knot_runtime: str = "auto",
    ) -> dict[str, Any]:
        normalized = self._normalize_row(dict(row), mode=mode)
        if normalized is None:
            raise ValueError("Candidate row is missing market or symbol.")
        candidate_rows = [normalized]
        self._apply_enrichment(
            candidate_rows,
            mode=mode,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        enriched = self.scoring_service.enrich_row(
            candidate_rows[0],
            mode=mode,
            generated_at=generated_at,
            as_of_date=as_of_date,
        )
        enriched["data_completeness"] = self._data_completeness(enriched)
        enriched["max_signal_score"] = self._max_signal_score(enriched.get("signals") or [])
        return enriched

    def resolve_sources(self, *, mode: str, sources: Iterable[str | Path] | None = None) -> list[Path]:
        if sources is not None:
            resolved = [Path(item) for item in sources if str(item).strip()]
            return [path for path in resolved if path.exists()]
        default_path = self._default_path_for_mode(mode)
        if default_path.exists():
            return [default_path]
        return []

    def _default_path_for_mode(self, mode: str) -> Path:
        if mode == "static":
            return self.runs_root / "candidate_inputs.json"
        return self.runs_root / "candidate_inputs.dynamic.json"

    def _collect_rows(
        self,
        *,
        mode: str,
        sources: list[Path],
        generated_at: str,
        as_of_date: str,
        include_market_data: bool,
        knot_runtime: str,
    ) -> tuple[list[dict[str, Any]], Counter, list[str], dict[str, Any]]:
        source_row_counts: Counter = Counter()
        warnings: list[str] = []
        enrichment_meta = {
            "market_data": {
                "enabled": bool(include_market_data),
                "status": "skipped" if not include_market_data else "pending",
            },
            "knot": {
                "enabled": str(knot_runtime or "auto").strip().lower() != "off",
                "runtime_mode": str(knot_runtime or "auto").strip().lower() or "auto",
                "status": "skipped" if str(knot_runtime or "auto").strip().lower() == "off" else "pending",
            },
        }
        if not sources:
            warnings.append(f"No source file was provided or found for {mode} candidate generation.")
            return [], source_row_counts, warnings, enrichment_meta

        raw_rows: list[dict[str, Any]] = []
        invalid_rows = 0
        for source_path in sources:
            payload = self._read_payload(source_path)
            items = payload.get("items", [])
            source_row_counts[str(source_path)] += len(items)
            for item in items:
                normalized = self._normalize_row(item, mode=mode)
                if normalized is None:
                    invalid_rows += 1
                    continue
                normalized["source_file"] = str(source_path)
                raw_rows.append(normalized)

        enrichment_result = self._apply_enrichment(
            raw_rows,
            mode=mode,
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        enrichment_meta.update(enrichment_result)
        warnings.extend(enrichment_result.get("warnings", []))

        rows: list[dict[str, Any]] = []
        for item in raw_rows:
            enriched = self.scoring_service.enrich_row(
                item,
                mode=mode,
                generated_at=generated_at,
                as_of_date=as_of_date,
            )
            enriched["source_file"] = str(item.get("source_file") or "")
            enriched["data_completeness"] = self._data_completeness(enriched)
            enriched["max_signal_score"] = self._max_signal_score(enriched.get("signals") or [])
            rows.append(enriched)
        if invalid_rows:
            warnings.append(f"Skipped {invalid_rows} invalid rows while generating {mode} candidates.")
        return rows, source_row_counts, list(dict.fromkeys(warnings)), enrichment_meta

    def _read_payload(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            items = data.get("items", [])
            return {
                "generated_at": data.get("generated_at"),
                "as_of_date": data.get("as_of_date"),
                "items": [dict(row) for row in items if isinstance(row, dict)],
            }
        if isinstance(data, list):
            return {"generated_at": None, "as_of_date": None, "items": [dict(row) for row in data if isinstance(row, dict)]}
        return {"generated_at": None, "as_of_date": None, "items": []}

    def _apply_enrichment(
        self,
        rows: list[dict[str, Any]],
        *,
        mode: str,
        include_market_data: bool,
        knot_runtime: str,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        if include_market_data:
            market_meta = self.market_data_service.enrich_rows(rows)
            warnings.extend(market_meta.get("warnings", []))
        else:
            market_meta = {
                "enabled": False,
                "status": "skipped",
                "source": getattr(self.market_data_service, "source_id", "market_data"),
                "matched_rows": 0,
                "warnings": [],
            }

        knot_runtime_mode = str(knot_runtime or "auto").strip().lower() or "auto"
        knot_meta = self.knot_service.enrich_rows(rows, mode=mode, runtime_mode=knot_runtime_mode)
        warnings.extend(knot_meta.get("warnings", []))
        return {
            "market_data": market_meta,
            "knot": knot_meta,
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _normalize_row(self, row: dict[str, Any], *, mode: str) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        market = str(row.get("market") or "").strip()
        symbol = normalize_symbol(str(row.get("symbol") or ""), market)
        if not market or not symbol:
            return None
        item = dict(row)
        item["candidate_type"] = mode
        item["market"] = market
        item["symbol"] = symbol
        item["name"] = str(item.get("name") or symbol).strip()
        if not item["name"]:
            item["name"] = symbol
        if not item.get("signals"):
            item["signals"] = []
        for field in _REQUIRED_TEXT_FIELDS:
            item[field] = str(item.get(field) or "").strip()
        if item.get("raw_score") not in {None, ""}:
            item["raw_score"] = normalize_score(item.get("raw_score"), default=0.5)
        return item

    def _dedupe_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("market") or ""), str(row.get("symbol") or ""))
            current = best.get(key)
            if current is None or self._rank_key(row) > self._rank_key(current):
                best[key] = row
        return list(best.values())

    def _rank_key(self, row: Mapping[str, Any]) -> tuple[float, float, float, str, str]:
        raw_score = normalize_score(row.get("raw_score"), default=0.0)
        consensus_score = normalize_score(row.get("consensus_score"), default=0.0)
        max_signal_score = self._max_signal_score(row.get("signals") or [])
        return (
            round(raw_score * 100 + consensus_score * 10 + max_signal_score * 5, 4),
            raw_score,
            consensus_score,
            str(row.get("market") or ""),
            str(row.get("symbol") or ""),
        )

    def _data_completeness(self, row: Mapping[str, Any]) -> float:
        checks = [
            bool(str(row.get("symbol") or "").strip()),
            bool(str(row.get("market") or "").strip()),
            bool(str(row.get("name") or "").strip()),
            row.get("raw_score") not in {None, ""},
            bool(str(row.get("rationale") or "").strip()),
            bool(str(row.get("risk") or "").strip()),
            bool(str(row.get("action_hint") or "").strip()),
            bool(row.get("signals")),
            bool(row.get("selection_policy")),
            bool(row.get("strategy_tags")),
            bool(row.get("source_breakdown")),
            bool(row.get("explanation_summary")),
        ]
        return round(sum(1 for item in checks if item) / len(checks), 2)

    def _max_signal_score(self, signals: list[dict[str, Any]]) -> float:
        if not signals:
            return 0.0
        return round(max(normalize_score(item.get("score"), default=0.0) for item in signals), 4)
