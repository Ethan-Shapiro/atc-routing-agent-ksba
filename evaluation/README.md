# Phase 5 — offline reward scoring

Scores a recorded time window ("episode") of `aircraft_state_history` / `anomaly_events` /
`coordination_events` against the reward function in `multi-agent_orchestration_rf_layer.md`
section 4. This is a replay/scoring tool, not a training loop — there's no RL policy here to
train, only past system behavior to grade. See `reward.py`'s module docstring for exactly
which terms are computed from real data and which are honest zeros (documented data gaps,
not fabricated numbers).

## Usage

```
docker compose --profile phase5 run --rm evaluation python score_episode.py \
    --start 2026-07-18T00:00:00Z --end 2026-07-18T01:00:00Z
```

Add `--json` for machine-readable output instead of the formatted report.
