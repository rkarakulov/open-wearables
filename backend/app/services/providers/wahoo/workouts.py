from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.constants.entry_source import get_unified_wahoo_entry_source
from app.constants.workout_types import get_unified_wahoo_workout_type
from app.database import DbSession
from app.schemas.enums import WorkoutType
from app.schemas.model_crud.activities import (
    EventRecordCreate,
    EventRecordDetailCreate,
    EventRecordMetrics,
)
from app.schemas.providers.wahoo import WahooWorkoutJSON, WahooWorkoutSummaryJSON
from app.services.event_record_service import event_record_service
from app.services.fit_parser import parse_fit_file
from app.services.providers.templates.base_workouts import BaseWorkoutsTemplate
from app.services.raw_payload_storage import store_fit_file
from app.utils.structured_logging import log_structured

_WORKOUT_LIST_KEYS = ("workouts", "data", "results")


def _extract_workouts(page_data: Any) -> list[dict[str, Any]]:
    """Wahoo's envelope is tolerated as either a bare array or an object wrapping it."""
    if isinstance(page_data, list):
        return [item for item in page_data if isinstance(item, dict)]
    if isinstance(page_data, dict):
        for key in _WORKOUT_LIST_KEYS:
            value = page_data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class WahooWorkouts(BaseWorkoutsTemplate):
    """Wahoo implementation of workouts template."""

    @property
    def events_per_page(self) -> int:
        """Get the number of workouts per page."""
        return settings.wahoo_events_per_page

    def get_workouts(
        self,
        db: DbSession,
        user_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> list[Any]:
        """Get workouts from the Wahoo API with page-based pagination.

        Wahoo's `/v1/workouts` has no since-cursor, so every page is fetched and
        filtered client-side by `starts` against [start_date, end_date].
        """
        all_workouts: list[dict[str, Any]] = []
        page = 1
        per_page = self.events_per_page

        while True:
            try:
                response = self._make_api_request(
                    db,
                    user_id,
                    # hard-coded value - update with base template changes
                    "/v1/workouts",
                    params={"page": page, "per_page": per_page},
                )
                workouts = _extract_workouts(response)
            except Exception as e:
                log_structured(
                    self.logger,
                    "error",
                    "Error fetching Wahoo workouts page",
                    provider="wahoo",
                    action="wahoo_fetch_page_error",
                    page=page,
                    user_id=str(user_id),
                    error=str(e),
                )
                if all_workouts:
                    log_structured(
                        self.logger,
                        "warning",
                        "Returning partial workout data due to error",
                        provider="wahoo",
                        action="wahoo_partial_data",
                        workouts_count=len(all_workouts),
                        user_id=str(user_id),
                        error=str(e),
                    )
                    break
                raise

            if not workouts:
                break

            reached_before_start = False
            for workout in workouts:
                starts = self._parse_starts(workout.get("starts"))
                if starts is not None and starts < start_date:
                    reached_before_start = True
                    continue
                if starts is not None and starts > end_date:
                    continue
                all_workouts.append(workout)

            if reached_before_start or len(workouts) < per_page:
                break
            page += 1

        return all_workouts

    def get_workouts_from_api(self, db: DbSession, user_id: UUID, **kwargs: Any) -> Any:
        """Get workouts from the Wahoo API with specific pagination options."""
        page = kwargs.get("page", 1)
        per_page = self.events_per_page

        # hard-coded value - update with base template changes
        return self._make_api_request(db, user_id, "/v1/workouts", params={"page": page, "per_page": per_page})

    def get_workout_detail_from_api(self, db: DbSession, user_id: UUID, workout_id: str, **kwargs: Any) -> Any:
        """Get a single detailed workout from the Wahoo API."""
        # hard-coded value - update with base template changes
        return self._make_api_request(db, user_id, f"/v1/workouts/{workout_id}")

    def _parse_starts(self, starts: str | None) -> datetime | None:
        """Parse Wahoo's `starts` ISO 8601 timestamp into an aware UTC datetime."""
        if not starts:
            return None
        try:
            parsed = datetime.fromisoformat(starts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _resolve_duration_seconds(self, workout: WahooWorkoutJSON, summary: WahooWorkoutSummaryJSON) -> int | None:
        """Prefer the summary's total accumulated duration; fall back to the workout's `minutes`."""
        if summary.duration_total_accum is not None:
            return int(summary.duration_total_accum)
        if workout.minutes is not None:
            return int(workout.minutes * 60)
        return None

    def _build_metrics(self, summary: WahooWorkoutSummaryJSON, name: str | None) -> EventRecordMetrics:
        """Build metrics from Wahoo workout_summary data.

        Only JSON-exposed accumulators are mapped — max heart rate/speed/power and
        elevation high/low are FIT-file-only on Wahoo and are not fabricated here.
        """
        metrics: EventRecordMetrics = {}

        if summary.heart_rate_avg is not None:
            metrics["heart_rate_avg"] = Decimal(str(summary.heart_rate_avg))

        if summary.distance_accum is not None:
            metrics["distance"] = Decimal(str(summary.distance_accum))

        if summary.speed_avg is not None:
            metrics["average_speed"] = Decimal(str(summary.speed_avg))

        if summary.cadence_avg is not None:
            metrics["average_cadence"] = Decimal(str(summary.cadence_avg))

        if summary.power_avg is not None:
            metrics["average_watts"] = Decimal(str(summary.power_avg))

        if summary.ascent_accum is not None:
            metrics["total_elevation_gain"] = Decimal(str(summary.ascent_accum))

        if summary.calories_accum is not None:
            metrics["energy_burned"] = Decimal(str(summary.calories_accum))

        if summary.duration_active_accum is not None:
            metrics["moving_time_seconds"] = int(summary.duration_active_accum)

        entry_source = get_unified_wahoo_entry_source(summary.manual)
        if entry_source is not None:
            metrics["entry_source"] = entry_source

        if name:
            metrics["label"] = name

        return metrics

    def _normalize_workout(
        self,
        raw_workout: WahooWorkoutJSON,
        user_id: UUID,
    ) -> tuple[EventRecordCreate, EventRecordDetailCreate]:
        """Normalize a Wahoo workout to EventRecordCreate and EventRecordDetailCreate."""
        workout_id = uuid4()

        summary = raw_workout.workout_summary or WahooWorkoutSummaryJSON()
        workout_type: WorkoutType = get_unified_wahoo_workout_type(raw_workout.workout_type_id)

        start_date = self._parse_starts(raw_workout.starts) or datetime.now(timezone.utc)
        duration_seconds = self._resolve_duration_seconds(raw_workout, summary)
        end_date = start_date + timedelta(seconds=duration_seconds) if duration_seconds is not None else start_date

        metrics = self._build_metrics(summary, raw_workout.name)

        record = EventRecordCreate(
            category="workout",
            type=workout_type.value,
            source_name="Wahoo",
            device_model="",
            duration_seconds=duration_seconds,
            start_datetime=start_date,
            end_datetime=end_date,
            zone_offset=None,  # Wahoo's `time_zone` format is not confirmed; not populated to avoid guessing.
            id=workout_id,
            external_id=str(raw_workout.id),
            source="wahoo",
            user_id=user_id,
        )

        detail = EventRecordDetailCreate(
            record_id=workout_id,
            **metrics,
        )

        return record, detail

    def _resolve_fit_url(self, raw_workout: WahooWorkoutJSON) -> str | None:
        """Extract the FIT file download URL from `workout_summary.file.url`, if present."""
        summary = raw_workout.workout_summary
        if summary is None or not summary.file:
            return None
        url = summary.file.get("url")
        return url if isinstance(url, str) and url else None

    def _download_fit_bytes(self, url: str) -> bytes | None:
        """Download a workout FIT file from Wahoo's CDN URL.

        No Authorization header: the URL is a short-lived, pre-signed CDN link,
        not an authenticated Wahoo API call.
        """
        try:
            response = httpx.get(url, timeout=settings.provider_request_timeout_seconds, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as e:
            log_structured(
                self.logger,
                "warning",
                "Failed to download Wahoo FIT file",
                provider="wahoo",
                action="wahoo_fit_download_failed",
                error=str(e),
            )
            return None

    def _enrich_workout_with_fit(
        self,
        db: DbSession,
        user_id: UUID,
        raw_workout: WahooWorkoutJSON,
        record_id: UUID,
    ) -> None:
        """Best-effort FIT enrichment: hr_zones/power_zones/segments onto the workout detail.

        The base workout (from workout_summary JSON averages) is already saved by the
        time this runs, so any failure here is logged and swallowed — it must never
        break workout ingestion.

        NOTE: some Wahoo FIT files carry left/right power balance and pedal smoothness,
        but OW's EventRecordDetail/fit_parser don't model those fields — out of scope here.
        """
        activity_id = str(raw_workout.id)
        try:
            fit_url = self._resolve_fit_url(raw_workout)
            if fit_url is None:
                # The list endpoint may omit workout_summary.file; fetch the full detail.
                detail_raw = self.get_workout_detail_from_api(db, user_id, activity_id)
                if isinstance(detail_raw, dict):
                    fit_url = self._resolve_fit_url(WahooWorkoutJSON(**detail_raw))

            if fit_url is None:
                return

            fit_bytes = self._download_fit_bytes(fit_url)
            if fit_bytes is None:
                return

            store_fit_file(provider="wahoo", fit_bytes=fit_bytes, user_id=str(user_id), activity_id=activity_id)
            fit_result = parse_fit_file(fit_bytes, user_id, source="wahoo")

            if not (fit_result.segments or fit_result.hr_zones or fit_result.power_zones):
                return

            fields: dict[str, Any] = {"segments": fit_result.segments}
            if fit_result.hr_zones is not None:
                fields["hr_zones"] = fit_result.hr_zones.model_dump()
            if fit_result.power_zones is not None:
                fields["power_zones"] = fit_result.power_zones.model_dump()

            event_record_service.event_record_detail_repo.update_workout_fields(db, record_id, fields)
            db.commit()
        except Exception as e:
            log_structured(
                self.logger,
                "warning",
                "Wahoo FIT enrichment failed, skipping",
                provider="wahoo",
                action="wahoo_fit_enrichment_failed",
                user_id=str(user_id),
                activity_id=activity_id,
                error=str(e),
            )

    def load_data(
        self,
        db: DbSession,
        user_id: UUID,
        **kwargs: Any,
    ) -> int:
        """Load data from the Wahoo API (historical backfill).

        Fetches all workouts in a date range using page-based pagination.
        """
        start = kwargs.get("start") or kwargs.get("start_date")
        end = kwargs.get("end") or kwargs.get("end_date")

        if not start:
            start_dt = datetime.now(timezone.utc) - timedelta(days=30)
        elif isinstance(start, datetime):
            start_dt = start
        elif isinstance(start, str):
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        else:
            start_dt = datetime.now(timezone.utc) - timedelta(days=30)

        if not end:
            end_dt = datetime.now(timezone.utc)
        elif isinstance(end, datetime):
            end_dt = end
        elif isinstance(end, str):
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        else:
            end_dt = datetime.now(timezone.utc)

        raw_workouts = self.get_workouts(db, user_id, start_dt, end_dt)

        parsed_workouts: list[WahooWorkoutJSON] = []
        for raw in raw_workouts:
            try:
                workout = WahooWorkoutJSON(**raw) if isinstance(raw, dict) else raw
                parsed_workouts.append(workout)
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to parse Wahoo workout",
                    provider="wahoo",
                    action="wahoo_parse_error",
                    user_id=str(user_id),
                    error=str(e),
                )

        count = 0
        for workout in parsed_workouts:
            record, detail = self._normalize_workout(workout, user_id)
            created_record = event_record_service.create(db, record)
            detail_for_record = detail.model_copy(update={"record_id": created_record.id})
            event_record_service.create_detail(db, detail_for_record)
            self._enrich_workout_with_fit(db, user_id, workout, created_record.id)
            count += 1

        return count

    def process_push_workout(
        self,
        db: DbSession,
        workout: WahooWorkoutJSON,
        user_id: UUID,
    ) -> list[UUID]:
        """Process a single workout pushed inline by the Wahoo webhook and save it.

        Args:
            db: Database session
            workout: Parsed Wahoo workout data (workout_summary already attached)
            user_id: Internal user ID (already mapped from the Wahoo user id)

        Returns:
            List of created event record IDs
        """
        created_ids: list[UUID] = []

        record, detail = self._normalize_workout(workout, user_id)
        created_record = event_record_service.create(db, record)
        detail_for_record = detail.model_copy(update={"record_id": created_record.id})
        event_record_service.create_detail(db, detail_for_record)
        created_ids.append(created_record.id)
        self._enrich_workout_with_fit(db, user_id, workout, created_record.id)

        return created_ids
