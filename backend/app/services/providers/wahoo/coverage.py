from app.schemas.enums import SeriesType
from app.schemas.enums.health_score_category import HealthScoreCategory

# Wahoo is workout/activity-focused only — no continuous ("247") data (no sleep, HRV,
# daily summaries). Per-sample timeseries are FIT-file-only, which Open Wearables does
# not parse for this provider, so no SeriesType values are declared either.
TIMESERIES: frozenset[SeriesType] = frozenset()

# EventRecordDetail fields populated by workouts.py from workout_summary data
# (GET /v1/workouts + GET /v1/workouts/{id}). Max heart rate/speed/power and
# elevation high/low are FIT-file-only and are not populated here.
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
    }
)

SLEEP_FIELDS: frozenset[str] = frozenset()

HEALTH_SCORES: frozenset[HealthScoreCategory] = frozenset()
