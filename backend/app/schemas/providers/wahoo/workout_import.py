from pydantic import BaseModel, ConfigDict


class WahooWorkoutSummaryJSON(BaseModel):
    """Wahoo workout_summary data from API responses or webhook pushes.

    Based on the Wahoo Cloud API workout_summary object. Max heart rate, max
    speed/power, and elevation high/low are not exposed here — Wahoo only
    provides those via the FIT file download, which Open Wearables does not
    parse for this provider.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    ascent_accum: float | None = None  # meters
    cadence_avg: float | None = None  # rpm
    calories_accum: float | None = None  # kcal
    distance_accum: float | None = None  # meters
    duration_active_accum: float | None = None  # seconds
    duration_paused_accum: float | None = None  # seconds
    duration_total_accum: float | None = None  # seconds
    heart_rate_avg: float | None = None  # bpm
    power_avg: float | None = None  # watts
    power_bike_np_last: float | None = None  # normalized power, watts
    power_bike_tss_last: float | None = None  # training stress score
    speed_avg: float | None = None  # meters/second
    work_accum: float | None = None  # joules
    manual: bool | None = None
    edited: bool | None = None
    file: dict | None = None  # {"url": "..."} — FIT file download URL


class WahooWorkoutJSON(BaseModel):
    """Wahoo workout data from API responses or webhook pushes.

    Based on the Wahoo Cloud API workout object (``GET /v1/workouts``,
    ``GET /v1/workouts/{id}``). The ``workout_summary`` is returned inline on
    detail fetches and webhook pushes; the list endpoint may omit it.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str | None = None
    starts: str  # ISO 8601 UTC
    minutes: float | None = None
    workout_type_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    workout_token: str | None = None
    workout_summary: WahooWorkoutSummaryJSON | None = None
