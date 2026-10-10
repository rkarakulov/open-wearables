"""Wahoo webhook handler.

Wahoo delivers data inline (PUSH mode): the complete `workout` and
`workout_summary` objects are included in the webhook body, so no REST fetch is
needed to process an event (unlike Strava/Oura's notify-only ping).

Verification scheme
--------------------
  Wahoo signs requests with a static shared secret delivered as a
  ``webhook_token`` field inside the JSON body itself (configured in the Wahoo
  developer portal), not a header-based HMAC signature.

Delivery model
--------------
  ``dispatch()`` acknowledges the event immediately (returns 200 quickly) and
  enqueues a Celery task. ``process_payload()`` normalizes and saves the
  inline workout data, called by the shared ``process_webhook_push`` task.

Supported event types
----------------------
  ``workout_summary`` (a ride/run/etc. finished uploading). Any other
  event_type is accepted and ignored.

See: https://cloud-api.wahooligan.com/ (Webhooks).
"""

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from celery import current_app as celery_app
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import DbSession
from app.repositories import UserConnectionRepository
from app.schemas.providers.wahoo import WahooWebhookEvent
from app.services.providers.templates.base_webhook_handler import BaseWebhookHandler
from app.services.providers.wahoo.workouts import WahooWorkouts
from app.services.raw_payload_storage import store_raw_payload
from app.utils.sentry_helpers import log_and_capture_error
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

_SUPPORTED_EVENT_TYPE = "workout_summary"
_PROCESS_PUSH_TASK = "app.integrations.celery.tasks.webhook_push_task.process_webhook_push"


class WahooWebhookHandler(BaseWebhookHandler):
    """Webhook handler for Wahoo's inline push events."""

    def __init__(self, workouts: WahooWorkouts) -> None:
        super().__init__("wahoo")
        self.workouts = workouts
        self.connection_repo = UserConnectionRepository()

    # ------------------------------------------------------------------
    # BaseWebhookHandler interface
    # ------------------------------------------------------------------

    def extract_user_id(self, payload: dict[str, Any]) -> str | None:
        """Wahoo's user id is nested under `user.id`, not a flat top-level field."""
        user = payload.get("user")
        if isinstance(user, dict) and user.get("id") is not None:
            return str(user["id"])
        return None

    def verify_signature(self, request: Request, body: bytes) -> bool:
        """Validate the shared `webhook_token` field carried in the JSON body."""
        expected_token = settings.wahoo_webhook_token.get_secret_value() if settings.wahoo_webhook_token else None
        if not expected_token:
            log_structured(
                logger,
                "warning",
                "WAHOO_WEBHOOK_TOKEN not configured — rejecting webhook",
                provider="wahoo",
                action="webhook_signature_missing_secret",
            )
            return False

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return False

        received_token = str(data.get("webhook_token") or "") if isinstance(data, dict) else ""
        if not received_token:
            return False

        return self._verify_token(expected_token, received_token)

    def parse_payload(self, body: bytes) -> WahooWebhookEvent:
        try:
            data = json.loads(body)
            return WahooWebhookEvent(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        except (ValidationError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {exc}") from exc

    def dispatch(self, db: DbSession, payload: WahooWebhookEvent) -> dict[str, Any]:
        """Store raw payload and enqueue async processing. Returns 200 immediately."""
        trace_id = str(uuid4())[:8]
        raw = payload.model_dump()

        log_structured(
            logger,
            "info",
            "Received Wahoo webhook event",
            provider="wahoo",
            trace_id=trace_id,
            event_type=payload.event_type,
            wahoo_user_id=payload.user.id,
            workout_id=payload.workout.id,
        )

        store_raw_payload(source="webhook", provider="wahoo", payload=raw, trace_id=trace_id)

        task = celery_app.send_task(_PROCESS_PUSH_TASK, args=["wahoo", raw, trace_id], queue="webhook_sync")
        log_structured(
            logger,
            "info",
            "Enqueued Wahoo webhook processing task",
            provider="wahoo",
            trace_id=trace_id,
            task_id=getattr(task, "id", None),
        )

        return {"status": "accepted"}

    def supported_event_types(self) -> list[str]:
        return [_SUPPORTED_EVENT_TYPE]

    # ------------------------------------------------------------------
    # Async processing (called by Celery task)
    # ------------------------------------------------------------------

    def process_payload(self, db: DbSession, payload: dict[str, Any], trace_id: str) -> dict[str, Any]:
        """Process a Wahoo PUSH payload synchronously.

        Called by the ``process_webhook_push`` Celery task with its own DB session.
        """
        try:
            event = WahooWebhookEvent(**payload)
        except (ValidationError, TypeError) as exc:
            log_and_capture_error(
                exc,
                logger,
                "Invalid Wahoo webhook payload",
                extra={
                    "provider": "wahoo",
                    "trace_id": trace_id,
                    "action": "webhook_invalid_payload",
                    "error": str(exc),
                },
            )
            return {"status": "error", "error": f"Invalid payload: {exc}"}

        if event.event_type != _SUPPORTED_EVENT_TYPE:
            log_structured(
                logger,
                "info",
                "Ignoring non-workout Wahoo event",
                provider="wahoo",
                trace_id=trace_id,
                event_type=event.event_type,
            )
            return {"status": "ignored", "reason": f"event_type:{event.event_type}"}

        wahoo_user_id = event.user.id
        connection = self.connection_repo.get_by_provider_user_id(db, "wahoo", str(wahoo_user_id))
        if not connection:
            log_structured(
                logger,
                "warning",
                "No connection found for Wahoo user",
                provider="wahoo",
                trace_id=trace_id,
                action="webhook_no_connection",
                wahoo_user_id=wahoo_user_id,
            )
            return {"status": "user_not_found", "wahoo_user_id": wahoo_user_id}

        user_id: UUID = connection.user_id
        self.connection_repo.update_last_synced_at(db, connection)

        workout = event.workout
        if event.workout_summary is not None and workout.workout_summary is None:
            workout = workout.model_copy(update={"workout_summary": event.workout_summary})

        log_structured(
            logger,
            "info",
            "Processing Wahoo webhook workout",
            provider="wahoo",
            trace_id=trace_id,
            user_id=str(user_id),
            wahoo_user_id=wahoo_user_id,
            workout_id=workout.id,
        )

        try:
            created_ids = self.workouts.process_push_workout(db=db, workout=workout, user_id=user_id)
            log_structured(
                logger,
                "info",
                "Wahoo workout saved",
                provider="wahoo",
                trace_id=trace_id,
                action="webhook_workout_saved",
                workout_id=workout.id,
                user_id=str(user_id),
                record_count=len(created_ids),
            )
            return {
                "status": "processed",
                "workout_id": workout.id,
                "records_saved": len(created_ids),
                "user_id": str(user_id),
            }

        except IntegrityError:
            db.rollback()
            log_structured(
                logger,
                "info",
                "Wahoo workout already exists, skipping",
                provider="wahoo",
                trace_id=trace_id,
                action="webhook_duplicate_workout",
                workout_id=workout.id,
                user_id=str(user_id),
            )
            return {
                "status": "ignored",
                "reason": "duplicate_workout",
                "workout_id": workout.id,
                "user_id": str(user_id),
            }

        except Exception as exc:
            log_and_capture_error(
                exc,
                logger,
                "Error processing Wahoo workout",
                extra={
                    "provider": "wahoo",
                    "trace_id": trace_id,
                    "action": "webhook_processing_error",
                    "workout_id": workout.id,
                    "user_id": str(user_id),
                    "error": str(exc),
                },
            )
            raise
