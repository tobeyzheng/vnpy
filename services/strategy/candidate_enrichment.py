from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from services.futu_account.quote_client import FutuQuoteClient

from .candidate_scoring import build_explanation_summary, is_generated_candidate_score_source, normalize_score
from .symbols import normalize_symbol

SUPPORTED_KNOT_RUNTIMES = {"off", "local", "remote", "auto"}

# OpenD's get_market_snapshot caps a single request at 400 codes; we keep a
# safety margin so retries / quote retransmissions don't push us over.
SNAPSHOT_BATCH_SIZE = 200
# When a batch fails we recursively halve it; below this size we assume the
# remaining symbol(s) are the offending ones and mark them as skipped instead
# of retrying further.
SNAPSHOT_MIN_BATCH_SIZE = 1


class CandidateMarketDataService:
    source_id = "futu_quote_snapshot"

    def __init__(self, quote_client: FutuQuoteClient | None = None):
        self.quote_client = quote_client or FutuQuoteClient()

    def enrich_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "enabled": True,
                "status": "no_rows",
                "source": self.source_id,
                "requested_symbols": 0,
                "matched_rows": 0,
                "warnings": [],
            }

        ok, message = self.quote_client.availability()
        if not ok:
            return {
                "enabled": True,
                "status": "unavailable",
                "source": self.source_id,
                "requested_symbols": len({self._row_symbol(row) for row in rows if self._row_symbol(row)}),
                "matched_rows": 0,
                "warnings": [message],
            }

        symbol_map: dict[str, str] = {}
        for row in rows:
            symbol = self._row_symbol(row)
            if not symbol:
                continue
            symbol_map[self._to_quote_code(symbol)] = symbol

        if not symbol_map:
            return {
                "enabled": True,
                "status": "no_symbols",
                "source": self.source_id,
                "requested_symbols": 0,
                "matched_rows": 0,
                "warnings": ["No valid symbols were available for market data enrichment."],
            }

        codes = list(symbol_map.keys())
        snapshot_rows, fetch_warnings, invalid_codes = self._fetch_snapshots_batched(codes)
        if not snapshot_rows and not invalid_codes and fetch_warnings:
            # Nothing came back at all and the failure isn't isolated to a few
            # bad symbols → treat as an outright fetch error so the caller can
            # decide whether to skip writing the pool.
            return {
                "enabled": True,
                "status": "error",
                "source": self.source_id,
                "requested_symbols": len(symbol_map),
                "matched_rows": 0,
                "warnings": fetch_warnings,
            }
        if not snapshot_rows and invalid_codes and len(invalid_codes) >= len(symbol_map):
            # Every symbol was quarantined → upstream is almost certainly
            # broken rather than each ticker being individually bad.
            preview = ", ".join(sorted(invalid_codes)[:5])
            return {
                "enabled": True,
                "status": "error",
                "source": self.source_id,
                "requested_symbols": len(symbol_map),
                "matched_rows": 0,
                "warnings": [
                    *fetch_warnings,
                    f"Quote snapshot fetch failed for every symbol; {len(invalid_codes)} quarantined (e.g. {preview}).",
                ],
            }

        snapshots: dict[str, dict[str, Any]] = {}
        for snapshot in snapshot_rows:
            normalized_snapshot = self._normalize_snapshot(dict(snapshot) if isinstance(snapshot, dict) else {})
            symbol = self._snapshot_symbol(normalized_snapshot)
            if symbol:
                snapshots[symbol] = normalized_snapshot

        matched_rows = 0
        for row in rows:
            symbol = self._row_symbol(row)
            if not symbol:
                continue
            snapshot = snapshots.get(symbol)
            if not snapshot:
                continue
            matched_rows += 1
            row["quote"] = dict(snapshot)
            self._apply_snapshot_aliases(row, snapshot)
            self._append_market_signal(row, snapshot)

        warnings: list[str] = list(fetch_warnings)
        if invalid_codes:
            preview = ", ".join(sorted(invalid_codes)[:5])
            warnings.append(
                f"Skipped {len(invalid_codes)} symbols that the snapshot service rejected: {preview}"
            )
        missing_symbols = sorted(set(symbol_map.values()) - set(snapshots.keys()))
        if missing_symbols:
            warnings.append(
                f"Snapshot lookup missed {len(missing_symbols)} symbols: {', '.join(missing_symbols[:5])}"
            )
        return {
            "enabled": True,
            "status": "ok",
            "source": self.source_id,
            "requested_symbols": len(symbol_map),
            "snapshot_count": len(snapshots),
            "matched_rows": matched_rows,
            "batch_size": SNAPSHOT_BATCH_SIZE,
            "invalid_symbol_count": len(invalid_codes),
            "warnings": warnings,
        }

    def _fetch_snapshots_batched(
        self, codes: list[str]
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """Pull market snapshots for ``codes`` in bounded batches.

        Returns ``(rows, warnings, invalid_codes)``:

        - ``rows`` are the raw snapshot dicts that came back successfully.
        - ``warnings`` describe transient batch failures that did not result
          in any quarantined symbol (e.g. transport hiccups).
        - ``invalid_codes`` lists single-symbol batches that the upstream
          rejected — these are quarantined and skipped, but do not abort
          the rest of the universe.
        """
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        invalid: list[str] = []
        if not codes:
            return rows, warnings, invalid
        for start in range(0, len(codes), SNAPSHOT_BATCH_SIZE):
            chunk = codes[start : start + SNAPSHOT_BATCH_SIZE]
            chunk_rows, chunk_warnings, chunk_invalid = self._fetch_chunk_with_split(chunk)
            rows.extend(chunk_rows)
            warnings.extend(chunk_warnings)
            invalid.extend(chunk_invalid)
        return rows, warnings, invalid

    def _fetch_chunk_with_split(
        self, codes: list[str]
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        if not codes:
            return [], [], []
        try:
            snapshot_rows = self.quote_client.get_snapshot(codes)
            return list(snapshot_rows or []), [], []
        except Exception as exc:
            if len(codes) <= SNAPSHOT_MIN_BATCH_SIZE:
                # Single bad symbol: quarantine and move on.
                return [], [], list(codes)
            mid = len(codes) // 2
            left_rows, left_warnings, left_invalid = self._fetch_chunk_with_split(codes[:mid])
            right_rows, right_warnings, right_invalid = self._fetch_chunk_with_split(codes[mid:])
            warnings = list(left_warnings) + list(right_warnings)
            if not left_rows and not right_rows and not (left_invalid or right_invalid):
                # Both halves fully failed without isolating a bad symbol →
                # surface a single concise warning so we don't spam the report.
                warnings.append(f"Quote snapshot fetch failed for {len(codes)} symbols: {exc}")
            return left_rows + right_rows, warnings, left_invalid + right_invalid

    def _row_symbol(self, row: Mapping[str, Any]) -> str:
        return normalize_symbol(str(row.get("symbol") or ""), str(row.get("market") or ""))

    def _to_quote_code(self, symbol: str) -> str:
        text = str(symbol or "").upper().strip()
        if not text or "." not in text:
            return text
        code, suffix = text.split(".", 1)
        return f"{suffix}.{code}"

    def _snapshot_symbol(self, snapshot: Mapping[str, Any]) -> str:
        code = str(snapshot.get("code") or snapshot.get("symbol") or "").upper().strip()
        market = str(snapshot.get("market") or "").strip().lower()
        if not code:
            return ""
        if "." in code:
            left, right = code.split(".", 1)
            if left in {"US", "HK", "SH", "SZ", "SHA", "SHE"}:
                inferred_market = market or ("hong_kong" if left == "HK" else "us" if left == "US" else "")
                return normalize_symbol(f"{right}.{left}", inferred_market)
        return normalize_symbol(code, market)

    def _normalize_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if snapshot.get("price") in {None, ""} and snapshot.get("last_price") not in {None, ""}:
            snapshot["price"] = snapshot.get("last_price")
        if snapshot.get("turnover_ratio") in {None, ""} and snapshot.get("turnover_rate") not in {None, ""}:
            snapshot["turnover_ratio"] = snapshot.get("turnover_rate")
        if snapshot.get("market_cap") in {None, ""}:
            for key in ("total_market_val", "market_val", "circ_market_val"):
                if snapshot.get(key) not in {None, ""}:
                    snapshot["market_cap"] = snapshot.get(key)
                    break
        return snapshot

    def _apply_snapshot_aliases(self, row: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
        for row_key, snapshot_keys in {
            "last_price": ("last_price", "price"),
            "price": ("price", "last_price"),
            "change_pct": ("change_pct",),
            "turnover": ("turnover",),
            "turnover_ratio": ("turnover_ratio", "turnover_rate"),
            "market_cap": ("market_cap", "total_market_val", "market_val", "circ_market_val"),
        }.items():
            if row.get(row_key) not in {None, ""}:
                continue
            for snapshot_key in snapshot_keys:
                value = snapshot.get(snapshot_key)
                if value not in {None, ""}:
                    row[row_key] = value
                    break

    def _append_market_signal(self, row: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
        signals = row.setdefault("signals", [])
        if not isinstance(signals, list):
            row["signals"] = signals = []
        change_pct = _as_float(snapshot.get("change_pct"))
        turnover = _as_float(snapshot.get("turnover"))
        if change_pct is None and turnover is None:
            return
        score = normalize_score(0.55 + max(-8.0, min(8.0, change_pct or 0.0)) / 30.0, default=0.55)
        summary_parts: list[str] = []
        if change_pct is not None:
            summary_parts.append(f"snapshot change {change_pct:.2f}%")
        if turnover is not None:
            summary_parts.append(f"turnover {turnover:.0f}")
        signals.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "market": str(row.get("market") or ""),
                "source": self.source_id,
                "category": "market_data",
                "score": score,
                "summary": "; ".join(summary_parts) or "market snapshot attached",
            }
        )


class CandidateKnotEnrichmentService:
    source_id = "knot_agent"

    def __init__(
        self,
        repo_root: Path,
        *,
        local_runtime: Any | None = None,
        remote_runtime: Any | None = None,
    ):
        self.repo_root = Path(repo_root)
        self._local_runtime = local_runtime
        self._remote_runtime = remote_runtime
        self._remote_init_error = ""

    def enrich_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        mode: str,
        runtime_mode: str = "auto",
        task_type: str = "strategy_select",
    ) -> dict[str, Any]:
        normalized_runtime = self._normalize_runtime(runtime_mode)
        if normalized_runtime == "off":
            return {
                "enabled": False,
                "status": "skipped",
                "source": self.source_id,
                "runtime_mode": normalized_runtime,
                "evaluated_rows": 0,
                "warnings": [],
            }
        if not rows:
            return {
                "enabled": True,
                "status": "no_rows",
                "source": self.source_id,
                "runtime_mode": normalized_runtime,
                "evaluated_rows": 0,
                "warnings": [],
            }

        runtime = self._runtime_for(normalized_runtime)
        if runtime is None:
            warning = self._remote_init_error or "Requested Knot runtime is unavailable."
            return {
                "enabled": True,
                "status": "unavailable",
                "source": self.source_id,
                "runtime_mode": normalized_runtime,
                "evaluated_rows": 0,
                "warnings": [warning],
            }

        evaluated_rows = 0
        fallback_count = 0
        runtimes_used: set[str] = set()
        warnings: list[str] = []
        for row in rows:
            symbol = normalize_symbol(str(row.get("symbol") or ""), str(row.get("market") or ""))
            market = str(row.get("market") or "").strip()
            if not symbol or not market:
                continue
            payload = self._build_payload(row, mode=mode)
            try:
                result = runtime.evaluate_symbol(
                    symbol=symbol,
                    market=market,
                    payload=payload,
                    task_type=task_type,
                )
            except Exception as exc:
                warnings.append(f"Knot evaluation failed for {symbol}: {exc}")
                continue
            evaluated_rows += 1
            if bool(result.get("fallback_used")):
                fallback_count += 1
            runtimes_used.add(str(result.get("runtime") or normalized_runtime))
            self._apply_result(row, result=result, mode=mode, task_type=task_type)

        actual_runtimes = sorted(runtimes_used)
        return {
            "enabled": True,
            "status": "ok",
            "source": self.source_id,
            "runtime_mode": normalized_runtime,
            "requested_runtime_mode": normalized_runtime,
            "runtimes_used": actual_runtimes,
            "single_runtime_effective": len(actual_runtimes) <= 1,
            "fallback_used": fallback_count > 0,
            "evaluated_rows": evaluated_rows,
            "fallback_count": fallback_count,
            "warnings": warnings,
        }

    def _normalize_runtime(self, runtime_mode: str) -> str:
        normalized = str(runtime_mode or "off").strip().lower()
        if normalized not in SUPPORTED_KNOT_RUNTIMES:
            return "off"
        return normalized

    def _runtime_for(self, runtime_mode: str) -> Any | None:
        if runtime_mode == "local":
            return self._local()
        if runtime_mode in {"remote", "auto"}:
            remote = self._remote()
            if remote is not None:
                return remote
            if runtime_mode == "auto":
                return self._local()
            return None
        return None

    def _local(self) -> Any:
        if self._local_runtime is None:
            from services.knot_runtime.runtime import KnotAgentRuntime

            self._local_runtime = KnotAgentRuntime(self.repo_root)
        return self._local_runtime

    def _remote(self) -> Any | None:
        if self._remote_runtime is not None:
            return self._remote_runtime
        try:
            from services.knot_runtime.remote_runtime import RemoteKnotAgentRuntime

            self._remote_runtime = RemoteKnotAgentRuntime(self.repo_root)
        except Exception as exc:
            self._remote_init_error = str(exc)
            return None
        return self._remote_runtime

    def _build_payload(self, row: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
        change_pct = _as_float(_nested_value(row, "quote.change_pct", "change_pct"))
        turnover = _as_float(_nested_value(row, "quote.turnover", "turnover", "avg_daily_turnover", "avg_daily_value"))
        raw_score = normalize_score(row.get("raw_score"), default=0.5)
        trend_score = normalize_score(row.get("trend_score"), default=self._trend_proxy(raw_score=raw_score, change_pct=change_pct))
        capital_score = normalize_score(row.get("flow_score", row.get("liquidity_score")), default=self._capital_proxy(turnover))
        risk_score = normalize_score(row.get("risk_penalty"), default=self._risk_proxy(row, change_pct=change_pct))
        summary = build_explanation_summary(row)
        return {
            "symbol": str(row.get("symbol") or ""),
            "market": str(row.get("market") or ""),
            "name": str(row.get("name") or row.get("symbol") or ""),
            "candidate_type": mode,
            "raw_score": raw_score,
            "trend_score": trend_score,
            "quality_score": normalize_score(row.get("quality_score"), default=raw_score),
            "capital_score": capital_score,
            "risk_score": risk_score,
            "rsi": _as_float(row.get("rsi")) or self._default_rsi(change_pct),
            "change_pct": change_pct,
            "turnover": turnover,
            "has_event_catalyst": bool(row.get("has_event_catalyst")),
            "near_resistance": bool(row.get("near_resistance")),
            "moving_average_bullish": bool(row.get("moving_average_bullish")) or trend_score >= 0.62,
            "missing_fields": self._missing_fields(row),
            "summary": summary,
            "risk": str(row.get("risk") or ""),
            "rationale": str(row.get("rationale") or ""),
            "signals": [
                {
                    "source": str(signal.get("source") or ""),
                    "category": str(signal.get("category") or ""),
                    "score": normalize_score(signal.get("score"), default=0.5),
                    "summary": str(signal.get("summary") or ""),
                }
                for signal in row.get("signals") or []
                if isinstance(signal, Mapping)
            ][:6],
        }

    def _apply_result(self, row: dict[str, Any], *, result: Mapping[str, Any], mode: str, task_type: str) -> None:
        parsed = dict(result.get("parsed") or {})
        strategy = str(parsed.get("strategy") or "watch_only")
        confidence = normalize_score(parsed.get("confidence"), default=0.65)
        reason = str(parsed.get("reason") or "").strip()
        risk_flags = [str(item).strip() for item in parsed.get("risk_flags", []) if str(item).strip()]
        signal_score = self._strategy_signal_score(strategy, confidence)
        knot_key = "knot_overlay_score" if mode == "dynamic" else "knot_research_score"
        if row.get(knot_key) in {None, ""}:
            row[knot_key] = signal_score
        row["strategy_selection"] = {
            "strategy_id": strategy,
            "allow_trade": strategy not in {"watch_only", "block_trade"},
            "confidence": confidence,
            "reason": reason or "knot candidate evaluation",
            "risk_flags": risk_flags,
            "source": str(result.get("runtime") or self.source_id),
        }
        row["knot_evaluation"] = {
            "task_type": task_type,
            "runtime": str(result.get("runtime") or self.source_id),
            "fallback_used": bool(result.get("fallback_used")),
            "schema_validated": bool(result.get("schema_validated", True)),
            "parsed": parsed,
        }
        current_source = str(row.get("confidence_source") or "").strip().lower()
        if not current_source or is_generated_candidate_score_source(current_source):
            row["confidence_source"] = str(result.get("runtime") or self.source_id)
        if reason and not str(row.get("research_note") or "").strip():
            row["research_note"] = reason
        if reason and not str(row.get("explanation_summary") or "").strip():
            row["explanation_summary"] = reason
        merged_flags = sorted(dict.fromkeys([*(row.get("risk_flags") or []), *risk_flags]))
        if merged_flags:
            row["risk_flags"] = merged_flags
        signals = row.setdefault("signals", [])
        if not isinstance(signals, list):
            row["signals"] = signals = []
        signals.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "market": str(row.get("market") or ""),
                "source": str(result.get("runtime") or self.source_id),
                "category": "research",
                "score": signal_score,
                "summary": reason or f"Knot suggested {strategy}",
                "metadata": {
                    "strategy": strategy,
                    "confidence": confidence,
                    "task_type": task_type,
                },
            }
        )

    def _strategy_signal_score(self, strategy: str, confidence: float) -> float:
        base = {
            "trend_following": 0.84,
            "breakout_momentum": 0.82,
            "pullback_buy": 0.78,
            "watch_only": 0.48,
            "block_trade": 0.2,
        }.get(strategy, 0.5)
        return normalize_score(base * 0.6 + confidence * 0.4, default=0.5)

    def _missing_fields(self, row: Mapping[str, Any]) -> int:
        missing = 0
        for key in ("rationale", "risk", "action_hint", "signals"):
            value = row.get(key)
            if key == "signals":
                if not isinstance(value, list) or not value:
                    missing += 1
                continue
            if value in {None, ""}:
                missing += 1
        return missing

    def _trend_proxy(self, *, raw_score: float, change_pct: float | None) -> float:
        if change_pct is None:
            return raw_score
        return normalize_score(0.5 + max(-8.0, min(8.0, change_pct)) / 20.0, default=raw_score)

    def _capital_proxy(self, turnover: float | None) -> float:
        if turnover is None:
            return 0.5
        if turnover >= 5_000_000_000:
            return 0.95
        if turnover >= 2_000_000_000:
            return 0.85
        if turnover >= 500_000_000:
            return 0.7
        if turnover >= 100_000_000:
            return 0.55
        return 0.35

    def _risk_proxy(self, row: Mapping[str, Any], *, change_pct: float | None) -> float:
        risk_level = str(row.get("risk_level") or "").strip().lower()
        if risk_level == "high":
            return 0.82
        if risk_level == "medium":
            return 0.58
        if change_pct is not None and abs(change_pct) >= 6:
            return 0.68
        return 0.38

    def _default_rsi(self, change_pct: float | None) -> float:
        if change_pct is None:
            return 55.0
        return max(35.0, min(82.0, 55.0 + change_pct * 2.0))


def _nested_value(row: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = row
        found = True
        for part in path.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in {None, ""}:
            return current
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
