# Candidate Provider Contract

## Purpose
Define a stable contract for any provider that feeds candidate inputs into the multi-strategy stock selection system.

Providers should NOT directly construct execution-layer objects.
They should emit normalized `CandidateInput` objects plus embedded `RawSignal` records.

## Required Fields for CandidateInput
- `symbol`: normalized symbol, e.g. `NVDA.US`, `00700.HK`, `688041.SH`
- `market`: one of `us`, `hong_kong`, `a_share`
- `name`: security display name
- `rationale`: concise human-readable thesis
- `risk`: primary downside / invalidation risk
- `raw_score`: numeric source score in provider's own context
- `confidence_source`: where the confidence/raw score comes from
- `action_hint`: suggested action hint before decision engine normalization
- `signals`: list of `RawSignal`

## Required Fields for RawSignal
- `symbol`
- `market`
- `source`
- `category`
- `score`
- `summary`

## Recommended Conventions
### confidence_source
Examples:
- `demo_provider`
- `technical_strategy_pack`
- `fundamental_scoring_model`
- `market_hotlist_engine`
- `composite_blend_v1`

### action_hint
Recommended values:
- `继续观察`
- `回调再买`
- `可小仓试错`
- `继续持有`
- `逢高减仓`
- `暂不介入`

### category
Recommended values:
- `technical`
- `fundamental`
- `event`
- `macro`
- `theme`
- `capital_flow`
- `risk`

## Design Rules
1. Providers are allowed to disagree with one another.
2. Composite provider is responsible for merging overlaps.
3. Decision engine is responsible for final action normalization.
4. Execution bridge must consume decision-layer outputs, not raw provider outputs.
5. If provider lacks some data, omit it honestly rather than fabricating fields.
