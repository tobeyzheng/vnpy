from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from vnpy_llm.base import beijing_now_isoformat

from .candidate_generation import HybridCandidateGenerationService
from .candidate_seed import KnotCandidateSeedService
from .universe import (
    DEFAULT_UNIVERSE_LIMIT,
    DEFAULT_UNIVERSE_PRESET,
    SUPPORTED_UNIVERSE_PRESETS,
    FutuMarketUniverseProvider,
    UniverseUnavailableError,
)

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

SUPPORTED_PREPARE_MARKETS = ("hong_kong", "us")
SUPPORTED_PREPARE_STRATEGIES = ("knot_first", "score_first", "merge_existing")
DEFAULT_TOP_N = 20
DEFAULT_KNOT_TARGET_COUNT = 20


class CandidateInputPreparationService:
    def __init__(
        self,
        repo_root: Path,
        *,
        seed_service: KnotCandidateSeedService | None = None,
        universe_provider: FutuMarketUniverseProvider | None = None,
    ):
        self.repo_root = Path(repo_root)
        self.runs_root = self.repo_root / "state" / "runs"
        # Legacy combined paths (read-only fallback only). The prepare
        # workflow now writes per-market files to avoid concurrent-overwrite
        # corruption when HK and US are refreshed in parallel.
        self.legacy_dynamic_path = self.runs_root / "candidate_inputs.dynamic.json"
        self.legacy_static_path = self.runs_root / "candidate_inputs.json"
        self.legacy_report_path = self.runs_root / "candidate_inputs.prepare.report.json"
        # Backwards-compatible attribute names retained for callers that
        # still reference them (treated as legacy fallbacks).
        self.dynamic_path = self.legacy_dynamic_path
        self.static_path = self.legacy_static_path
        self.report_path = self.legacy_report_path
        self.generation_service = HybridCandidateGenerationService(self.repo_root)
        self.seed_service = seed_service or KnotCandidateSeedService()
        self._universe_provider = universe_provider

    # ------------------------------------------------------------------
    # Per-market path helpers
    # ------------------------------------------------------------------
    def dynamic_path_for(self, market: str) -> Path:
        return self.runs_root / f"candidate_inputs.dynamic.{market}.json"

    def static_path_for(self, market: str) -> Path:
        return self.runs_root / f"candidate_inputs.static.{market}.json"

    def report_path_for(self, market: str) -> Path:
        return self.runs_root / f"candidate_inputs.prepare.report.{market}.json"

    @property
    def universe_provider(self) -> FutuMarketUniverseProvider:
        if self._universe_provider is None:
            self._universe_provider = FutuMarketUniverseProvider()
        return self._universe_provider

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

        # Legacy prepare() now produces per-market dynamic + static files.
        # Source resolution still honours the legacy combined paths so that
        # historical inputs continue to work.
        resolved_dynamic_sources = self._resolve_sources(dynamic_sources, fallback=self.legacy_dynamic_path)
        resolved_static_sources = self._resolve_sources(static_sources, fallback=self.legacy_static_path)

        targets: dict[str, dict[str, Any]] = {}
        all_warnings: list[str] = []
        market_coverage: set[str] = set()
        total_items = 0
        missing_field_counts: dict[str, int] = {}
        report_paths: list[str] = []

        for market_name in SUPPORTED_PREPARE_MARKETS:
            dyn_target = self.dynamic_path_for(market_name)
            sta_target = self.static_path_for(market_name)
            dyn_result = self._prepare_target(
                target_path=dyn_target,
                mode="dynamic",
                sources=resolved_dynamic_sources,
                generated_at=generated_at,
                as_of_date=as_of_date,
                include_market_data=include_market_data,
                knot_runtime=knot_runtime,
                market_filter=market_name,
            )
            sta_result = self._prepare_target(
                target_path=sta_target,
                mode="static",
                sources=resolved_static_sources,
                generated_at=generated_at,
                as_of_date=as_of_date,
                include_market_data=include_market_data,
                knot_runtime=knot_runtime,
                market_filter=market_name,
            )
            targets[f"dynamic_{market_name}"] = dyn_result
            targets[f"static_{market_name}"] = sta_result
            for piece in (dyn_result, sta_result):
                all_warnings.extend(piece.get("warnings", []))
                market_coverage.update(piece.get("market_coverage", []))
                total_items += int(piece.get("item_count", 0) or 0)
                missing_field_counts = self._merge_counters(
                    missing_field_counts,
                    piece.get("missing_required_field_counts", {}),
                )

        # Per-market reports (avoid concurrent overwrite when HK and US run
        # in parallel). The legacy combined report path is still written as
        # a back-compat aggregate.
        for market_name in SUPPORTED_PREPARE_MARKETS:
            per_market_report = {
                "schema_version": "candidate_prepare_report_v2",
                "generated_at": generated_at,
                "as_of_date": as_of_date,
                "market": market_name,
                "report_path": str(self.report_path_for(market_name)),
                "targets": {
                    "dynamic": targets.get(f"dynamic_{market_name}", {}),
                    "static": targets.get(f"static_{market_name}", {}),
                },
            }
            self.report_path_for(market_name).write_text(
                json.dumps(self._sanitize_json_data(per_market_report), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            report_paths.append(str(self.report_path_for(market_name)))

        report = {
            "schema_version": "candidate_prepare_report_v2",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "report_path": str(self.legacy_report_path),
            "per_market_report_paths": report_paths,
            "targets": targets,
            "summary": {
                "written_targets": [name for name, item in targets.items() if item.get("written")],
                "market_coverage": sorted(market_coverage),
                "provider_merge_policy": "symbol_merge_dynamic_preferred",
                "total_items": total_items,
                "include_market_data": bool(include_market_data),
                "knot_runtime": str(knot_runtime or "auto"),
                "warnings": list(dict.fromkeys(all_warnings)),
                "missing_required_field_counts": missing_field_counts,
            },
        }
        sanitized_report = self._sanitize_json_data(report)
        self.legacy_report_path.write_text(
            json.dumps(sanitized_report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return sanitized_report

    def prepare_market(
        self,
        *,
        market: str = "all",
        strategy: str = "knot_first",
        top_n: int = DEFAULT_TOP_N,
        knot_target_count: int = DEFAULT_KNOT_TARGET_COUNT,
        knot_runtime: str = "auto",
        include_market_data: bool = True,
        universe_limit: int = DEFAULT_UNIVERSE_LIMIT,
        universe_preset: str = DEFAULT_UNIVERSE_PRESET,
        dry_run: bool = False,
        generated_at: str | None = None,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Refresh the dynamic candidate pool one market at a time.

        - ``market='all'`` → run for every supported market in turn (HK + US),
          merging each market's results back into ``candidate_inputs.dynamic.json``
          without disturbing rows that belong to other markets.
        - ``strategy='knot_first'`` (default) → ask the remote Knot agent for
          a short list of candidates, then score/enrich them locally. If Knot
          is unavailable the workflow automatically downgrades to
          ``score_first`` for that market.
        - ``strategy='score_first'`` → enumerate the full Futu universe,
          score everything locally, keep the Top-N and re-run the Knot
          enrichment pass on the survivors.
        - ``strategy='merge_existing'`` → re-process the rows already present
          in the dynamic pool (preserves the legacy refresh semantics).

        The static pool is intentionally **not** touched in this method; the
        prepare-static refresh remains a separate (monthly) workflow.
        """
        generated_at = generated_at or beijing_now_isoformat()
        as_of_date = as_of_date or generated_at.split("T", 1)[0]
        self.runs_root.mkdir(parents=True, exist_ok=True)

        strategy_key = (strategy or "knot_first").strip().lower()
        if strategy_key not in SUPPORTED_PREPARE_STRATEGIES:
            raise ValueError(
                f"Unsupported prepare strategy: {strategy}; expected one of {SUPPORTED_PREPARE_STRATEGIES}."
            )
        market_key = (market or "all").strip().lower()
        if market_key == "all":
            target_markets = list(SUPPORTED_PREPARE_MARKETS)
        else:
            if market_key not in SUPPORTED_PREPARE_MARKETS:
                raise ValueError(
                    f"Unsupported market: {market}; expected one of {SUPPORTED_PREPARE_MARKETS} or 'all'."
                )
            target_markets = [market_key]

        bounded_top_n = max(1, int(top_n or DEFAULT_TOP_N))
        bounded_target_count = max(bounded_top_n, int(knot_target_count or DEFAULT_KNOT_TARGET_COUNT))
        bounded_universe_limit = max(50, int(universe_limit or DEFAULT_UNIVERSE_LIMIT))
        preset_key = (universe_preset or DEFAULT_UNIVERSE_PRESET).strip().lower()
        if preset_key not in SUPPORTED_UNIVERSE_PRESETS:
            preset_key = DEFAULT_UNIVERSE_PRESET

        market_runs: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        market_dynamic_paths: dict[str, str] = {}
        market_report_paths: dict[str, str] = {}
        total_items = 0
        market_counts: Counter = Counter()
        scoring_meta = self.generation_service.scoring_service.model_metadata("dynamic")

        for market_name in target_markets:
            run_result = self._prepare_single_market(
                market=market_name,
                strategy=strategy_key,
                top_n=bounded_top_n,
                knot_target_count=bounded_target_count,
                knot_runtime=knot_runtime,
                include_market_data=include_market_data,
                universe_limit=bounded_universe_limit,
                universe_preset=preset_key,
                generated_at=generated_at,
                as_of_date=as_of_date,
            )
            market_runs.append(run_result)
            all_warnings.extend(run_result.get("warnings", []))

            new_rows = list(run_result.get("rows", []))
            dyn_target_path = self.dynamic_path_for(market_name)
            rep_target_path = self.report_path_for(market_name)
            market_dynamic_paths[market_name] = str(dyn_target_path)
            market_report_paths[market_name] = str(rep_target_path)

            deduped_rows = self.generation_service._dedupe_rows(new_rows)
            sorted_rows = sorted(deduped_rows, key=self.generation_service._rank_key, reverse=True)
            market_market_counts = Counter(
                str(row.get("market") or "") for row in sorted_rows if row.get("market")
            )
            market_counts.update(market_market_counts)
            total_items += len(sorted_rows)

            payload: dict[str, Any] = {
                "mode": "dynamic_generated",
                "schema_version": "candidate_inputs_v3",
                "generated_at": generated_at,
                "as_of_date": as_of_date,
                "market": market_name,
                "selection_policy": scoring_meta["selection_policy"],
                "scoring_model": scoring_meta,
                "source_files": sorted(set(run_result.get("source_files", []))),
                "source_row_counts": {},
                "item_count": len(sorted_rows),
                "market_coverage": sorted(market_market_counts.keys()),
                "market_counts": dict(market_market_counts),
                "items": sorted_rows,
                "warnings": list(dict.fromkeys(run_result.get("warnings", []))),
                "row_requirements": {
                    "required_fields": list(REQUIRED_ROW_FIELDS),
                    "recommended_fields": list(RECOMMENDED_ROW_FIELDS),
                },
                "preparation_metadata": {
                    "prepared_by": self.__class__.__name__,
                    "prepared_mode": "dynamic_market_scoped",
                    "report_path": str(rep_target_path),
                    "include_market_data": bool(include_market_data),
                    "knot_runtime": str(knot_runtime or "auto"),
                    "strategy": strategy_key,
                    "target_market": market_name,
                    "top_n": bounded_top_n,
                    "knot_target_count": bounded_target_count,
                    "universe_limit": bounded_universe_limit,
                    "universe_preset": preset_key,
                    "dry_run": bool(dry_run),
                },
            }
            sanitized_payload = self._sanitize_json_data(payload)

            if not dry_run:
                dyn_target_path.write_text(
                    json.dumps(sanitized_payload, ensure_ascii=False, indent=2, allow_nan=False),
                    encoding="utf-8",
                )
                run_result["written"] = True
            else:
                run_result["written"] = False
            run_result["output_path"] = str(dyn_target_path)

            per_market_report = {
                "schema_version": "candidate_prepare_report_v3",
                "generated_at": generated_at,
                "as_of_date": as_of_date,
                "report_path": str(rep_target_path),
                "mode": "dynamic_market_scoped",
                "market": market_name,
                "strategy_requested": strategy_key,
                "dynamic_path": str(dyn_target_path),
                "dry_run": bool(dry_run),
                "written": bool(run_result.get("written")),
                "market_run": self._strip_run_rows(run_result),
                "summary": {
                    "total_items": len(sorted_rows),
                    "market_counts": dict(market_market_counts),
                    "include_market_data": bool(include_market_data),
                    "knot_runtime": str(knot_runtime or "auto"),
                    "warnings": list(dict.fromkeys(run_result.get("warnings", []))),
                },
            }
            rep_target_path.write_text(
                json.dumps(self._sanitize_json_data(per_market_report), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )

        report = {
            "schema_version": "candidate_prepare_report_v3",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "report_path": str(self.legacy_report_path),
            "per_market_report_paths": market_report_paths,
            "per_market_dynamic_paths": market_dynamic_paths,
            "mode": "dynamic_market_scoped",
            "strategy_requested": strategy_key,
            "target_markets": list(target_markets),
            "dynamic_path": str(self.legacy_dynamic_path),
            "dry_run": bool(dry_run),
            "written": (not dry_run) and bool(target_markets),
            "market_runs": [self._strip_run_rows(run) for run in market_runs],
            "summary": {
                "total_items": total_items,
                "market_counts": dict(market_counts),
                "include_market_data": bool(include_market_data),
                "knot_runtime": str(knot_runtime or "auto"),
                "warnings": list(dict.fromkeys(all_warnings)),
            },
        }
        sanitized_report = self._sanitize_json_data(report)
        # Legacy aggregate report retained as a back-compat marker; per-market
        # reports above are the authoritative artifacts.
        self.legacy_report_path.write_text(
            json.dumps(sanitized_report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return sanitized_report

    def _prepare_single_market(
        self,
        *,
        market: str,
        strategy: str,
        top_n: int,
        knot_target_count: int,
        knot_runtime: str,
        include_market_data: bool,
        universe_limit: int,
        universe_preset: str = DEFAULT_UNIVERSE_PRESET,
        generated_at: str,
        as_of_date: str,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        strategy_used = strategy
        knot_status = "skipped"
        knot_seed_count = 0
        universe_size = 0

        seed_rows: list[dict[str, Any]] = []
        if strategy == "knot_first":
            seed_result = self.seed_service.generate_candidates(
                market=market,
                target_count=knot_target_count,
                runtime_mode=knot_runtime,
            )
            knot_seed_count = len(seed_result.rows)
            if seed_result.is_empty():
                strategy_used = "score_first"
                knot_status = f"unavailable_fallback_to_score_first:{seed_result.fallback_reason or 'unknown'}"
                warnings.append(
                    f"Knot seed unavailable for {market}; downgraded to score_first ({seed_result.fallback_reason})."
                )
            else:
                knot_status = "ok"
                seed_rows = seed_result.rows
        elif strategy == "merge_existing":
            seed_rows = self._existing_rows_for_market(market)
            if not seed_rows:
                strategy_used = "score_first"
                warnings.append(
                    f"No existing rows for {market}; downgraded merge_existing to score_first."
                )

        if strategy_used == "score_first":
            universe_rows = self._invoke_universe_provider(
                market=market,
                preset=universe_preset,
                limit=universe_limit,
            )
            if universe_rows is None:
                warnings.append(
                    f"Universe unavailable for {market}; skipped score_first refresh."
                )
                return {
                    "market": market,
                    "strategy_requested": strategy,
                    "strategy_used": strategy_used,
                    "knot_status": knot_status,
                    "knot_seed_count": knot_seed_count,
                    "universe_size": 0,
                    "universe_preset": universe_preset,
                    "scored_count": 0,
                    "kept_count": 0,
                    "rows": [],
                    "source_files": [],
                    "warnings": warnings,
                }
            if universe_limit and len(universe_rows) > universe_limit:
                universe_rows = universe_rows[:universe_limit]
            seed_rows = [self._universe_row_to_seed(item) for item in universe_rows]
            universe_size = len(seed_rows)

        if not seed_rows:
            return {
                "market": market,
                "strategy_requested": strategy,
                "strategy_used": strategy_used,
                "knot_status": knot_status,
                "knot_seed_count": knot_seed_count,
                "universe_size": universe_size,
                "scored_count": 0,
                "kept_count": 0,
                "rows": [],
                "source_files": [],
                "warnings": warnings,
            }

        tmp_source = self.runs_root / f"candidate_inputs.dynamic.{market}.seed.json"
        tmp_payload = {
            "schema_version": "candidate_inputs_v3_seed",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "market": market,
            "strategy_used": strategy_used,
            "items": seed_rows,
        }
        tmp_source.write_text(
            json.dumps(self._sanitize_json_data(tmp_payload), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        try:
            generated = self.generation_service.generate(
                mode="dynamic",
                sources=[tmp_source],
                generated_at=generated_at,
                as_of_date=as_of_date,
                include_market_data=include_market_data,
                knot_runtime=knot_runtime,
            )
        finally:
            try:
                tmp_source.unlink()
            except FileNotFoundError:
                pass

        scored_items = [
            item for item in generated.get("items", [])
            if isinstance(item, dict) and str(item.get("market") or "").strip().lower() == market
        ]
        kept = scored_items[:top_n]
        warnings.extend(generated.get("warnings", []))

        # Zero-enrichment guard: when score_first ran against the live
        # universe but every snapshot fetch failed (or got quarantined), we
        # would otherwise fall back to alphabetic top-N. Detect that case via
        # the enrichment metadata embedded in ``generated`` and refuse to
        # commit those rows so the existing dynamic pool stays untouched.
        if (
            strategy_used == "score_first"
            and include_market_data
            and self._enrichment_failed(generated)
        ):
            warnings.append(
                f"Snapshot enrichment for {market} produced no matches; refusing to overwrite the dynamic pool with unscored rows."
            )
            return {
                "market": market,
                "strategy_requested": strategy,
                "strategy_used": strategy_used,
                "knot_status": knot_status,
                "knot_seed_count": knot_seed_count,
                "universe_size": universe_size,
                "universe_preset": universe_preset,
                "scored_count": len(scored_items),
                "kept_count": 0,
                "rows": [],
                "source_files": [str(tmp_source)],
                "warnings": list(dict.fromkeys(warnings)),
            }

        return {
            "market": market,
            "strategy_requested": strategy,
            "strategy_used": strategy_used,
            "knot_status": knot_status,
            "knot_seed_count": knot_seed_count,
            "universe_size": universe_size,
            "universe_preset": universe_preset,
            "scored_count": len(scored_items),
            "kept_count": len(kept),
            "rows": kept,
            "source_files": [str(tmp_source)],
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _invoke_universe_provider(
        self,
        *,
        market: str,
        preset: str,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        """Call the configured provider, tolerating older stub signatures.

        Returns ``None`` when the provider could not be used (treated as a
        soft failure by the caller). Returns a list of raw universe rows
        otherwise.
        """
        provider = self.universe_provider
        try:
            try:
                return list(
                    provider.list_symbols(market, preset=preset, extra_limit=limit)
                )
            except TypeError:
                # Backward compatibility: legacy stubs / providers that only
                # accept a single positional ``market`` argument.
                return list(provider.list_symbols(market))
        except (UniverseUnavailableError, ValueError):
            return None
        except Exception:
            return None

    @staticmethod
    def _enrichment_failed(generated: dict[str, Any]) -> bool:
        enrichment = generated.get("enrichment")
        if not isinstance(enrichment, Mapping):
            return False
        market_data = enrichment.get("market_data") if isinstance(enrichment.get("market_data"), Mapping) else enrichment
        status = str(market_data.get("status") or "").strip().lower()
        if status in {"error", "unavailable"}:
            return True
        try:
            requested = int(market_data.get("requested_symbols") or 0)
            matched = int(market_data.get("matched_rows") or 0)
        except (TypeError, ValueError):
            return False
        return requested > 0 and matched == 0

    def _strip_run_rows(self, run: dict[str, Any]) -> dict[str, Any]:
        stripped = {key: value for key, value in run.items() if key != "rows"}
        stripped["top_symbols"] = [
            {"symbol": str(row.get("symbol") or ""), "raw_score": row.get("raw_score")}
            for row in run.get("rows", [])[:10]
        ]
        return stripped

    def _load_dynamic_payload(self, market: str | None = None) -> dict[str, Any]:
        """Return rows from the dynamic candidate pool for ``market``.

        Reads the per-market file ``candidate_inputs.dynamic.{market}.json``
        when available, falling back to the legacy combined file (filtering
        by market when provided).
        """
        market_key = (market or "").strip().lower() or None
        candidate_paths: list[Path] = []
        if market_key is not None:
            candidate_paths.append(self.dynamic_path_for(market_key))
        candidate_paths.append(self.legacy_dynamic_path)

        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                items = data.get("items", [])
            elif isinstance(data, list):
                items = data
            else:
                items = []
            normalized = [dict(row) for row in items if isinstance(row, dict)]
            if market_key is not None:
                normalized = [
                    row
                    for row in normalized
                    if str(row.get("market") or "").strip().lower() == market_key
                ]
            return {"items": normalized}
        return {"items": []}

    def _existing_rows_for_market(self, market: str) -> list[dict[str, Any]]:
        payload = self._load_dynamic_payload(market=market)
        return [
            dict(row)
            for row in payload.get("items", [])
            if isinstance(row, dict)
            and str(row.get("market") or "").strip().lower() == market
        ]

    def _universe_row_to_seed(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": row.get("symbol"),
            "market": row.get("market"),
            "name": row.get("name") or row.get("symbol"),
            "candidate_type": "dynamic",
            "raw_score": 0.5,
            "rationale": "",
            "risk": "",
            "action_hint": "",
            "signals": [],
            "confidence_source": "futu_universe",
        }

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
        market_filter: str | None = None,
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
        if market_filter is not None:
            market_key = market_filter.strip().lower()
            filtered_items = [
                row
                for row in payload.get("items", [])
                if isinstance(row, dict)
                and str(row.get("market") or "").strip().lower() == market_key
            ]
            payload["items"] = filtered_items
            payload["item_count"] = len(filtered_items)
            new_counts = Counter(
                str(row.get("market") or "") for row in filtered_items if row.get("market")
            )
            payload["market_counts"] = dict(new_counts)
            payload["market_coverage"] = sorted(new_counts.keys())
            payload["market_filter"] = market_key
        if market_filter is not None and not payload.get("items"):
            warnings.append(
                f"No rows for market={market_filter} in source(s); wrote empty {target_path.name}."
            )
        payload["row_requirements"] = {
            "required_fields": list(REQUIRED_ROW_FIELDS),
            "recommended_fields": list(RECOMMENDED_ROW_FIELDS),
        }
        payload["preparation_metadata"] = {
            "prepared_by": self.__class__.__name__,
            "prepared_mode": mode,
            "report_path": str(
                self.report_path_for(market_filter) if market_filter is not None else self.legacy_report_path
            ),
            "include_market_data": bool(include_market_data),
            "knot_runtime": str(knot_runtime or "auto"),
            "market_filter": market_filter,
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
