from pydantic import BaseModel

from app.schemas.providers.wahoo.workout_import import WahooWorkoutJSON, WahooWorkoutSummaryJSON


class WahooWebhookUser(BaseModel):
    """The athlete identity embedded in a Wahoo webhook push."""

    id: int


class WahooWebhookEvent(BaseModel):
    """Wahoo webhook push payload (full data, not a notify-only ping).

    Wahoo pushes the complete workout and workout_summary inline as sibling
    top-level fields (unlike the REST detail response, where workout_summary
    is nested under workout), so no REST fetch is needed to process it. The
    shared secret is a static ``webhook_token`` field carried in the JSON
    body itself (configured in the Wahoo developer portal), not a header
    signature.

    See: https://cloud-api.wahooligan.com/ (Webhooks).
    """

    event_type: str
    webhook_token: str | None = None
    user: WahooWebhookUser
    workout: WahooWorkoutJSON
    workout_summary: WahooWorkoutSummaryJSON | None = None
