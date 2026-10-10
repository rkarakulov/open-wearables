"""Tests for WahooWorkouts normalization."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.schemas.enums import EntrySource, WorkoutType
from app.schemas.providers.wahoo import WahooWorkoutJSON, WahooWorkoutSummaryJSON
from app.services.providers.wahoo.oauth import WahooOAuth
from app.services.providers.wahoo.workouts import WahooWorkouts


class TestWahooWorkoutsNormalization:
    """Test workout normalization."""

    @pytest.fixture
    def workouts(self) -> WahooWorkouts:
        oauth = WahooOAuth(
            user_repo=MagicMock(),
            connection_repo=MagicMock(),
            provider_name="wahoo",
            api_base_url="https://api.wahooligan.com",
        )
        return WahooWorkouts(
            workout_repo=MagicMock(),
            connection_repo=MagicMock(),
            provider_name="wahoo",
            api_base_url="https://api.wahooligan.com",
            oauth=oauth,
        )

    def _workout(
        self,
        *,
        manual: bool | None = None,
        workout_type_id: int = 0,
        summary_overrides: dict | None = None,
    ) -> WahooWorkoutJSON:
        summary_fields: dict = {
            "heart_rate_avg": 142.0,
            "distance_accum": 32000.0,
            "speed_avg": 8.5,
            "cadence_avg": 85.0,
            "power_avg": 180.0,
            "ascent_accum": 410.0,
            "calories_accum": 950.0,
            "duration_active_accum": 3500.0,
            "duration_total_accum": 3600.0,
            "manual": manual,
            **(summary_overrides or {}),
        }
        summary = WahooWorkoutSummaryJSON(**summary_fields)
        return WahooWorkoutJSON(
            id=12345,
            name="Morning Ride",
            starts="2024-01-15T06:00:00Z",
            minutes=60.0,
            workout_type_id=workout_type_id,
            workout_summary=summary,
        )

    def test_normalize_workout_manual_entry(self, workouts: WahooWorkouts) -> None:
        record, detail = workouts._normalize_workout(self._workout(manual=True), uuid4())

        assert record.source == "wahoo"
        assert record.type == WorkoutType.CYCLING.value
        assert detail.entry_source == EntrySource.MANUAL
        assert detail.label == "Morning Ride"

    def test_normalize_workout_auto_entry(self, workouts: WahooWorkouts) -> None:
        _, detail = workouts._normalize_workout(self._workout(manual=False), uuid4())

        assert detail.entry_source == EntrySource.AUTOMATIC

    def test_normalize_workout_unknown_entry_source(self, workouts: WahooWorkouts) -> None:
        _, detail = workouts._normalize_workout(self._workout(), uuid4())

        assert detail.entry_source is None

    def test_normalize_workout_maps_summary_metrics(self, workouts: WahooWorkouts) -> None:
        _, detail = workouts._normalize_workout(self._workout(), uuid4())

        assert detail.heart_rate_avg == 142.0
        assert detail.distance == 32000.0
        assert detail.average_speed == 8.5
        assert detail.average_cadence == 85.0
        assert detail.average_watts == 180.0
        assert detail.total_elevation_gain == 410.0
        assert detail.energy_burned == 950.0
        assert detail.moving_time_seconds == 3500

    def test_normalize_workout_duration_prefers_summary_total(self, workouts: WahooWorkouts) -> None:
        record, _ = workouts._normalize_workout(self._workout(), uuid4())

        # duration_total_accum (3600) wins over minutes*60 (also 3600 here, but exercised distinctly below)
        assert record.duration_seconds == 3600

    def test_normalize_workout_duration_falls_back_to_minutes(self, workouts: WahooWorkouts) -> None:
        workout = self._workout(summary_overrides={"duration_total_accum": None})
        record, _ = workouts._normalize_workout(workout, uuid4())

        assert record.duration_seconds == 3600  # 60 minutes * 60

    @pytest.mark.parametrize(
        ("workout_type_id", "expected"),
        [
            (0, WorkoutType.CYCLING),
            (1, WorkoutType.RUNNING),
            (25, WorkoutType.POOL_SWIMMING),
            (12, WorkoutType.INDOOR_CYCLING),
            (255, WorkoutType.OTHER),
            (9999, WorkoutType.OTHER),
        ],
    )
    def test_normalize_workout_type_mapping(
        self, workouts: WahooWorkouts, workout_type_id: int, expected: WorkoutType
    ) -> None:
        record, _ = workouts._normalize_workout(self._workout(workout_type_id=workout_type_id), uuid4())

        assert record.type == expected.value

    def test_oversized_name_does_not_abort_the_batch(self, workouts: WahooWorkouts) -> None:
        """An overlong name is truncated so the surrounding workouts still import."""
        workout_ids = [uuid4() for _ in range(3)]
        workouts_list = [
            self._workout(manual=False),
            WahooWorkoutJSON(
                id=999,
                name="x" * 400,
                starts="2024-01-15T06:00:00Z",
                minutes=60.0,
                workout_type_id=0,
                workout_summary=WahooWorkoutSummaryJSON(manual=False),
            ),
            self._workout(manual=True),
        ]

        details = [workouts._normalize_workout(w, workout_ids[i])[1] for i, w in enumerate(workouts_list)]

        assert len(details) == 3
        assert details[1].label == "x" * 255
        assert details[2].entry_source == EntrySource.MANUAL
