# Candidate Input Example

The file provider reads candidate inputs from JSON.

Default expected locations (per-market layout, primary):
- `state/runs/candidate_inputs.dynamic.{hong_kong,us}.json`
- `state/runs/candidate_inputs.static.{hong_kong,us}.json`

Legacy combined locations (read-only fallback during the migration window):
- `state/runs/candidate_inputs.dynamic.json`
- `state/runs/candidate_inputs.json`

A sample file is provided at:
- `examples/candidate_inputs.sample.json`

Recommended workflow:
1. upstream strategy engines write normalized candidate inputs into JSON
2. file provider reads them
3. composite provider merges them with other providers
4. decision engine produces action suggestions
5. approval gate + risk engine decide whether paper intents are allowed
