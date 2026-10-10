"""Tests for Wahoo FIT-file enrichment (hr_zones / power_zones / segments / session metrics)."""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.schemas.model_crud.activities.zones import HRZone, HRZones, PowerZone, PowerZones
from app.schemas.providers.wahoo import WahooWorkoutJSON, WahooWorkoutSummaryJSON
from app.services.fit_parser import FitParseResult, SessionMetrics
from app.services.providers.wahoo.oauth import WahooOAuth
from app.services.providers.wahoo.workouts import WahooWorkouts


class TestWahooFitEnrichment:
    """Test FIT download/parse/detail-update wiring for Wahoo workouts."""

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

    def _workout(self, *, file_url: str | None = "https://cdn.wahooligan.com/fit/abc.fit") -> WahooWorkoutJSON:
        summary = WahooWorkoutSummaryJSON(file={"url": file_url} if file_url else None)
        return WahooWorkoutJSON(
            id=12345,
            name="Morning Ride",
            starts="2024-01-15T06:00:00Z",
            minutes=60.0,
            workout_type_id=0,
            workout_summary=summary,
        )

    # -- _resolve_fit_url -------------------------------------------------

    def test_resolve_fit_url_present(self, workouts: WahooWorkouts) -> None:
        assert workouts._resolve_fit_url(self._workout()) == "https://cdn.wahooligan.com/fit/abc.fit"

    def test_resolve_fit_url_missing_file(self, workouts: WahooWorkouts) -> None:
        workout = self._workout(file_url=None)
        assert workouts._resolve_fit_url(workout) is None

    def test_resolve_fit_url_missing_summary(self, workouts: WahooWorkouts) -> None:
        workout = WahooWorkoutJSON(id=1, starts="2024-01-15T06:00:00Z", workout_summary=None)
        assert workouts._resolve_fit_url(workout) is None

    # -- _enrich_workout_with_fit ------------------------------------------

    @patch("app.services.providers.wahoo.workouts.event_record_service")
    @patch("app.services.providers.wahoo.workouts.store_fit_file")
    @patch("app.services.providers.wahoo.workouts.parse_fit_file")
    def test_enrich_updates_detail_with_zones_and_segments(
        self,
        mock_parse_fit_file: MagicMock,
        mock_store_fit_file: MagicMock,
        mock_event_record_service: MagicMock,
        workouts: WahooWorkouts,
    ) -> None:
        """hr_zones, power_zones and segments found in the FIT are written to the detail."""
        hr_zones = HRZones(zones=[HRZone(zone=1, seconds=120.0, max_bpm=140)], max_hr=190, threshold_hr=170)
        power_zones = PowerZones(zones=[PowerZone(zone=1, seconds=90.0, max_watts=200)], ftp_watts=250)
        segments = [{"kind": "lap", "index": 0, "elapsed_seconds": 600}]
        mock_parse_fit_file.return_value = FitParseResult(segments=segments, hr_zones=hr_zones, power_zones=power_zones)
        workouts._download_fit_bytes = MagicMock(return_value=b"FIT_BYTES")

        user_id = uuid4()
        record_id = uuid4()
        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=user_id, raw_workout=self._workout(), record_id=record_id
        )

        mock_store_fit_file.assert_called_once()
        assert mock_store_fit_file.call_args.kwargs["provider"] == "wahoo"
        assert mock_store_fit_file.call_args.kwargs["fit_bytes"] == b"FIT_BYTES"
        assert mock_store_fit_file.call_args.kwargs["activity_id"] == "12345"

        mock_parse_fit_file.assert_called_once()

        update_mock = mock_event_record_service.event_record_detail_repo.update_workout_fields
        update_mock.assert_called_once()
        call_args = update_mock.call_args.args
        assert call_args[1] == record_id
        fields = call_args[2]
        assert fields["segments"] == segments
        assert fields["hr_zones"] == hr_zones.model_dump()
        assert fields["power_zones"] == power_zones.model_dump()

    @patch("app.services.providers.wahoo.workouts.event_record_service")
    @patch("app.services.providers.wahoo.workouts.store_fit_file")
    @patch("app.services.providers.wahoo.workouts.parse_fit_file")
    def test_enrich_updates_detail_with_session_power_metrics(
        self,
        mock_parse_fit_file: MagicMock,
        mock_store_fit_file: MagicMock,
        mock_event_record_service: MagicMock,
        workouts: WahooWorkouts,
    ) -> None:
        """Session-level power metrics (max power/speed/elevation, L/R balance, pedal/torque %) are persisted."""
        session_metrics = SessionMetrics(
            max_watts=Decimal("450"),
            max_speed=Decimal("12.5"),
            elev_high=Decimal("250"),
            elev_low=Decimal("180"),
            left_right_balance=Decimal("52"),
            avg_left_pedal_smoothness=Decimal("20.5"),
            avg_right_pedal_smoothness=Decimal("21.5"),
            avg_left_torque_effectiveness=Decimal("80"),
            avg_right_torque_effectiveness=Decimal("82"),
        )
        mock_parse_fit_file.return_value = FitParseResult(session_metrics=session_metrics)
        workouts._download_fit_bytes = MagicMock(return_value=b"FIT_BYTES")

        record_id = uuid4()
        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(), record_id=record_id
        )

        update_mock = mock_event_record_service.event_record_detail_repo.update_workout_fields
        update_mock.assert_called_once()
        call_args = update_mock.call_args.args
        assert call_args[1] == record_id
        fields = call_args[2]
        assert fields["max_watts"] == Decimal("450")
        assert fields["max_speed"] == Decimal("12.5")
        assert fields["elev_high"] == Decimal("250")
        assert fields["elev_low"] == Decimal("180")
        assert fields["left_right_balance"] == Decimal("52")
        assert fields["avg_left_pedal_smoothness"] == Decimal("20.5")
        assert fields["avg_right_pedal_smoothness"] == Decimal("21.5")
        assert fields["avg_left_torque_effectiveness"] == Decimal("80")
        assert fields["avg_right_torque_effectiveness"] == Decimal("82")

    @patch("app.services.providers.wahoo.workouts.event_record_service")
    @patch("app.services.providers.wahoo.workouts.store_fit_file")
    @patch("app.services.providers.wahoo.workouts.parse_fit_file")
    def test_enrich_runs_when_only_session_metrics_present(
        self,
        mock_parse_fit_file: MagicMock,
        mock_store_fit_file: MagicMock,
        mock_event_record_service: MagicMock,
        workouts: WahooWorkouts,
    ) -> None:
        """Detail update fires even when segments/zones are absent but session metrics were parsed."""
        mock_parse_fit_file.return_value = FitParseResult(session_metrics=SessionMetrics(max_watts=Decimal("300")))
        workouts._download_fit_bytes = MagicMock(return_value=b"FIT_BYTES")

        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(), record_id=uuid4()
        )

        mock_event_record_service.event_record_detail_repo.update_workout_fields.assert_called_once()

    @patch("app.services.providers.wahoo.workouts.event_record_service")
    @patch("app.services.providers.wahoo.workouts.store_fit_file")
    @patch("app.services.providers.wahoo.workouts.parse_fit_file")
    def test_enrich_skips_detail_update_when_fit_has_no_zones_or_segments(
        self,
        mock_parse_fit_file: MagicMock,
        mock_store_fit_file: MagicMock,
        mock_event_record_service: MagicMock,
        workouts: WahooWorkouts,
    ) -> None:
        mock_parse_fit_file.return_value = FitParseResult()
        workouts._download_fit_bytes = MagicMock(return_value=b"FIT_BYTES")

        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(), record_id=uuid4()
        )

        mock_event_record_service.event_record_detail_repo.update_workout_fields.assert_not_called()

    def test_enrich_is_noop_when_no_fit_url_resolvable(self, workouts: WahooWorkouts) -> None:
        """No file.url on the item and the detail re-fetch also lacks one: enrichment skips quietly."""
        workouts.get_workout_detail_from_api = MagicMock(return_value={"id": 12345, "starts": "2024-01-15T06:00:00Z"})
        workouts._download_fit_bytes = MagicMock()

        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(file_url=None), record_id=uuid4()
        )

        workouts._download_fit_bytes.assert_not_called()

    def test_enrich_falls_back_to_detail_endpoint_when_list_item_lacks_file(self, workouts: WahooWorkouts) -> None:
        """List endpoint omitted workout_summary.file; the detail endpoint is fetched to get it."""
        detail_payload = {
            "id": 12345,
            "starts": "2024-01-15T06:00:00Z",
            "workout_summary": {"file": {"url": "https://cdn.wahooligan.com/fit/from-detail.fit"}},
        }
        workouts.get_workout_detail_from_api = MagicMock(return_value=detail_payload)
        workouts._download_fit_bytes = MagicMock(return_value=None)

        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(file_url=None), record_id=uuid4()
        )

        workouts.get_workout_detail_from_api.assert_called_once()
        workouts._download_fit_bytes.assert_called_once_with("https://cdn.wahooligan.com/fit/from-detail.fit")

    def test_enrich_never_raises_on_download_failure(self, workouts: WahooWorkouts) -> None:
        """Best-effort: a download exception is swallowed, never propagated to the caller."""
        workouts._download_fit_bytes = MagicMock(side_effect=RuntimeError("boom"))

        workouts._enrich_workout_with_fit(
            db=MagicMock(), user_id=uuid4(), raw_workout=self._workout(), record_id=uuid4()
        )

    def test_download_fit_bytes_returns_none_on_http_error(self, workouts: WahooWorkouts) -> None:
        with patch("app.services.providers.wahoo.workouts.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectTimeout("timed out")
            assert workouts._download_fit_bytes("https://cdn.wahooligan.com/fit/abc.fit") is None

    def test_download_fit_bytes_sends_no_authorization_header(self, workouts: WahooWorkouts) -> None:
        """Wahoo's FIT CDN URL is a pre-signed link — no Bearer token is sent."""
        with patch("app.services.providers.wahoo.workouts.httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.content = b"FIT_BYTES"
            mock_get.return_value = mock_response

            result = workouts._download_fit_bytes("https://cdn.wahooligan.com/fit/abc.fit")

            assert result == b"FIT_BYTES"
            _, kwargs = mock_get.call_args
            assert "headers" not in kwargs
