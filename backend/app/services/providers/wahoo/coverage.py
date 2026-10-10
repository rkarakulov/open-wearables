from app.schemas.enums import SeriesType
from app.schemas.enums.health_score_category import HealthScoreCategory

# Wahoo is workout/activity-focused only — no continuous ("247") data (no sleep, HRV,
# daily summaries). Per-sample timeseries are FIT-file-only, which Open Wearables does
# not parse for this provider, so no SeriesType values are declared either.
TIMESERIES: frozenset[SeriesType] = frozenset()

# EventRecordDetail fields populated by workouts.py: the base set from workout_summary
# JSON data (GET /v1/workouts + GET /v1/workouts/{id}), plus a best-effort FIT-file
# enrichment pass (hr_zones/power_zones/segments and FIT `session`-message aggregates —
# see app.services.fit_parser.SessionMetrics) that may leave the latter group unset when
# no FIT file is available for a workout.
WORKOUT_FIELDS: frozenset[str] = frozenset(
    {
        "heart_rate_avg",
        "distance",
        "average_speed",
        "average_cadence",
        "average_watts",
        "total_elevation_gain",
        "energy_burned",
        "moving_time_seconds",
        "entry_source",
        "label",
        "hr_zones",
        "power_zones",
        "segments",
        "max_watts",
        "max_speed",
        "elev_high",
        "elev_low",
        "left_right_balance",
        "avg_left_pedal_smoothness",
        "avg_right_pedal_smoothness",
        "avg_left_torque_effectiveness",
        "avg_right_torque_effectiveness",
    }
)

SLEEP_FIELDS: frozenset[str] = frozenset()

HEALTH_SCORES: frozenset[HealthScoreCategory] = frozenset()
