from decimal import Decimal
from uuid import uuid4

import fitdecode
import pytest

from app.schemas.enums.series_types import SeriesType
from app.services.fit_parser import (
    FitParseResult,
    SessionMetrics,
    _decode_left_right_balance,
    parse_fit_file,
    session_metrics_fields,
)
from tests.fixtures.fit_builder import (
    make_cycling_fit,
    make_cycling_power_fit,
    make_cycling_zones_fit,
    make_running_fit,
    make_swimming_fit,
)

USER_ID = uuid4()
DS_ID = uuid4()


@pytest.fixture(scope="module")
def running() -> FitParseResult:
    return parse_fit_file(make_running_fit(), USER_ID, DS_ID, source="garmin")


@pytest.fixture(scope="module")
def cycling() -> FitParseResult:
    return parse_fit_file(make_cycling_fit(), USER_ID, DS_ID, source="garmin")


@pytest.fixture(scope="module")
def cycling_power() -> FitParseResult:
    return parse_fit_file(make_cycling_power_fit(), USER_ID, DS_ID, source="garmin")


@pytest.fixture(scope="module")
def swimming() -> FitParseResult:
    return parse_fit_file(make_swimming_fit(), USER_ID, DS_ID, source="garmin")


class TestRunning:
    def test_sample_count(self, running: FitParseResult) -> None:
        assert len(running.samples) > 0

    def test_expected_series_types(self, running: FitParseResult) -> None:
        types = {s.series_type for s in running.samples}
        assert SeriesType.heart_rate in types
        assert SeriesType.speed in types
        assert SeriesType.cadence in types
        assert SeriesType.power in types
        assert SeriesType.running_vertical_oscillation in types
        assert SeriesType.running_ground_contact_time in types
        assert SeriesType.latitude in types
        assert SeriesType.longitude in types
        assert SeriesType.elevation in types
        assert SeriesType.air_temperature in types
        assert SeriesType.running_vertical_ratio in types
        assert SeriesType.running_stance_time_balance in types

    def test_granularity_one_second(self, running: FitParseResult) -> None:
        hr = sorted(s.recorded_at for s in running.samples if s.series_type == SeriesType.heart_rate)
        gaps = [(hr[i + 1] - hr[i]).seconds for i in range(len(hr) - 1)]
        assert all(g == 1 for g in gaps)

    def test_heart_rate_values_in_range(self, running: FitParseResult) -> None:
        vals = [s.value for s in running.samples if s.series_type == SeriesType.heart_rate]
        assert all(Decimal(40) <= v <= Decimal(220) for v in vals)

    def test_speed_non_negative(self, running: FitParseResult) -> None:
        vals = [s.value for s in running.samples if s.series_type == SeriesType.speed]
        assert all(v >= Decimal(0) for v in vals)

    def test_vertical_oscillation_in_cm_range(self, running: FitParseResult) -> None:
        # FIT raw mm/10 → parser _scale(0.1) → cm; synthetic value 8.5 cm
        vals = [s.value for s in running.samples if s.series_type == SeriesType.running_vertical_oscillation]
        assert all(Decimal("0.1") <= v <= Decimal("30") for v in vals)

    def test_sample_metadata(self, running: FitParseResult) -> None:
        s = running.samples[0]
        assert s.user_id == USER_ID
        assert s.data_source_id == DS_ID
        assert s.source == "garmin"
        assert s.recorded_at is not None
        assert s.recorded_at.tzinfo is not None

    def test_gps_values_in_degree_range(self, running: FitParseResult) -> None:
        lats = [s.value for s in running.samples if s.series_type == SeriesType.latitude]
        lons = [s.value for s in running.samples if s.series_type == SeriesType.longitude]
        assert all(Decimal(-90) <= v <= Decimal(90) for v in lats)
        assert all(Decimal(-180) <= v <= Decimal(180) for v in lons)

    def test_elevation_in_meters(self, running: FitParseResult) -> None:
        vals = [s.value for s in running.samples if s.series_type == SeriesType.elevation]
        # synthetic: 200 m
        assert all(Decimal(0) <= v <= Decimal(9000) for v in vals)

    def test_temperature_in_celsius_range(self, running: FitParseResult) -> None:
        vals = [s.value for s in running.samples if s.series_type == SeriesType.air_temperature]
        # synthetic: 18°C
        assert all(Decimal(-50) <= v <= Decimal(60) for v in vals)

    def test_running_dynamics_in_range(self, running: FitParseResult) -> None:
        vr = [s.value for s in running.samples if s.series_type == SeriesType.running_vertical_ratio]
        stb = [s.value for s in running.samples if s.series_type == SeriesType.running_stance_time_balance]
        # synthetic: vertical_ratio=8.5%, stance_time_balance=49.5%
        assert all(Decimal(0) <= v <= Decimal(100) for v in vr)
        assert all(Decimal(0) <= v <= Decimal(100) for v in stb)


class TestCycling:
    def test_only_heart_rate(self, cycling: FitParseResult) -> None:
        assert {s.series_type for s in cycling.samples} == {SeriesType.heart_rate}

    def test_has_samples(self, cycling: FitParseResult) -> None:
        assert len(cycling.samples) > 0


class TestSwimming:
    def test_only_heart_rate(self, swimming: FitParseResult) -> None:
        assert {s.series_type for s in swimming.samples} == {SeriesType.heart_rate}


class TestSegments:
    def test_running_has_two_laps(self, running: FitParseResult) -> None:
        laps = [s for s in running.segments if s["kind"] == "lap"]
        assert len(laps) == 2

    def test_lap_required_fields(self, running: FitParseResult) -> None:
        for lap in running.segments:
            assert lap["kind"] == "lap"
            assert isinstance(lap["index"], int)
            assert lap["elapsed_seconds"] > 0
            assert lap["start_time"] is not None

    def test_lap_has_distance_and_hr(self, running: FitParseResult) -> None:
        lap = running.segments[0]
        assert "distance_meters" in lap
        assert lap["distance_meters"] > 0
        assert "avg_heart_rate" in lap
        assert 40 <= lap["avg_heart_rate"] <= 220
        assert "max_heart_rate" in lap
        assert lap["max_heart_rate"] >= lap["avg_heart_rate"]

    def test_lap_indices_sequential(self, running: FitParseResult) -> None:
        laps = [s for s in running.segments if s["kind"] == "lap"]
        indices = [lap["index"] for lap in laps]
        assert indices == list(range(len(laps)))

    def test_cycling_no_segments(self, cycling: FitParseResult) -> None:
        assert cycling.segments == []

    def test_swimming_no_segments(self, swimming: FitParseResult) -> None:
        assert swimming.segments == []


class TestInvalidInput:
    def test_empty_bytes_returns_no_samples(self) -> None:
        assert parse_fit_file(b"", uuid4(), uuid4()).samples == []

    def test_garbage_bytes_raises(self) -> None:
        with pytest.raises(fitdecode.FitError):
            parse_fit_file(b"not a fit file at all", uuid4(), uuid4())


class TestSessionMetrics:
    def test_no_session_message_leaves_none(self, cycling: FitParseResult) -> None:
        # make_cycling_fit has no session message at all
        assert cycling.session_metrics is None

    def test_running_has_no_power_balance(self, running: FitParseResult) -> None:
        # make_running_fit has laps but no session message
        assert running.session_metrics is None

    def test_session_message_populates_metrics(self, cycling_power: FitParseResult) -> None:
        metrics = cycling_power.session_metrics
        assert metrics is not None
        assert metrics.max_watts == Decimal("450")
        assert metrics.max_speed == Decimal("12.5")
        assert metrics.elev_high == Decimal("250")
        assert metrics.elev_low == Decimal("180")

    def test_left_right_balance_decoded_as_right_percent(self, cycling_power: FitParseResult) -> None:
        metrics = cycling_power.session_metrics
        assert metrics is not None
        assert metrics.left_right_balance == Decimal("52")

    def test_pedal_smoothness_and_torque_effectiveness(self, cycling_power: FitParseResult) -> None:
        metrics = cycling_power.session_metrics
        assert metrics is not None
        assert metrics.avg_left_torque_effectiveness == Decimal("80")
        assert metrics.avg_right_torque_effectiveness == Decimal("82")
        assert metrics.avg_left_pedal_smoothness == Decimal("20.5")
        assert metrics.avg_right_pedal_smoothness == Decimal("21.5")


class TestDecodeLeftRightBalance:
    def test_none_raw_returns_none(self) -> None:
        assert _decode_left_right_balance(None) is None

    def test_right_flag_unset_returns_none(self) -> None:
        # mask bits set (pct * 100 = 5000) but the "right" flag (0x8000) is NOT set,
        # so the side the percentage applies to isn't reliable.
        assert _decode_left_right_balance(5000) is None

    def test_right_flag_set_decodes_percent(self) -> None:
        raw = 5000 | 0x8000
        assert _decode_left_right_balance(raw) == Decimal("50")

    def test_non_numeric_raw_returns_none(self) -> None:
        assert _decode_left_right_balance("not-a-number") is None


class TestSessionMetricsFields:
    def test_none_metrics_returns_empty_dict(self) -> None:
        assert session_metrics_fields(None) == {}

    def test_filters_out_none_fields(self) -> None:
        metrics = SessionMetrics(max_watts=Decimal("300"), max_speed=None)
        assert session_metrics_fields(metrics) == {"max_watts": Decimal("300")}

    def test_all_fields_present(self) -> None:
        metrics = SessionMetrics(
            max_watts=Decimal("300"),
            max_speed=Decimal("10"),
            elev_high=Decimal("200"),
            elev_low=Decimal("100"),
            left_right_balance=Decimal("51"),
            avg_left_pedal_smoothness=Decimal("20"),
            avg_right_pedal_smoothness=Decimal("21"),
            avg_left_torque_effectiveness=Decimal("79"),
            avg_right_torque_effectiveness=Decimal("81"),
        )
        fields = session_metrics_fields(metrics)
        assert set(fields) == {
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


class TestSessionZoneFallback:
    """Zones read from the session message when a FIT has no dedicated time_in_zone
    message (Wahoo-style): seconds per zone, no boundaries, whole-activity max_hr/FTP."""

    @pytest.fixture(scope="class")
    def result(self) -> FitParseResult:
        return parse_fit_file(
            make_cycling_zones_fit(
                hr_seconds=[10.0, 20.0, 30.0, 0.0, 0.0],
                power_seconds=[5.0, 15.0, 25.0],
                max_heart_rate=182,
                threshold_power=255,
            ),
            USER_ID,
            DS_ID,
            source="wahoo",
        )

    def test_hr_zones_from_session(self, result: FitParseResult) -> None:
        assert result.hr_zones is not None
        # trailing all-zero zones stripped
        assert [z.seconds for z in result.hr_zones.zones] == [10.0, 20.0, 30.0]
        assert all(z.max_bpm is None for z in result.hr_zones.zones)
        assert [z.zone for z in result.hr_zones.zones] == [0, 1, 2]
        assert result.hr_zones.max_hr == 182

    def test_power_zones_from_session(self, result: FitParseResult) -> None:
        assert result.power_zones is not None
        assert [z.seconds for z in result.power_zones.zones] == [5.0, 15.0, 25.0]
        assert all(z.max_watts is None for z in result.power_zones.zones)
        assert result.power_zones.ftp_watts == 255

    def test_session_without_zone_arrays_stays_none(self, cycling_power: FitParseResult) -> None:
        # make_cycling_power_fit has a session message but no time-in-zone arrays —
        # the fallback must not fabricate zones.
        assert cycling_power.hr_zones is None
        assert cycling_power.power_zones is None
