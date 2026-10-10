from .webhook import WahooWebhookEvent, WahooWebhookUser
from .workout_import import WahooWorkoutJSON, WahooWorkoutSummaryJSON

__all__ = [
    # Workout import
    "WahooWorkoutJSON",
    "WahooWorkoutSummaryJSON",
    # Webhook
    "WahooWebhookEvent",
    "WahooWebhookUser",
]
