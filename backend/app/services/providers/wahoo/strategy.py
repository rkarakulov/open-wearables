from app.services.providers.base_strategy import BaseProviderStrategy, ProviderCapabilities, ProviderCoverage
from app.services.providers.wahoo.coverage import HEALTH_SCORES, SLEEP_FIELDS, TIMESERIES, WORKOUT_FIELDS
from app.services.providers.wahoo.oauth import WahooOAuth
from app.services.providers.wahoo.webhook_handler import WahooWebhookHandler
from app.services.providers.wahoo.workouts import WahooWorkouts


class WahooStrategy(BaseProviderStrategy):
    """Wahoo provider implementation."""

    def __init__(self):
        super().__init__()

        # Initialize OAuth component
        self.oauth = WahooOAuth(
            user_repo=self.user_repo,
            connection_repo=self.connection_repo,
            provider_name=self.name,
            api_base_url=self.api_base_url,
        )

        # Initialize workouts component
        self.workouts = WahooWorkouts(
            workout_repo=self.workout_repo,
            connection_repo=self.connection_repo,
            provider_name=self.name,
            api_base_url=self.api_base_url,
            oauth=self.oauth,
        )

        # Wahoo has no continuous monitoring data (no sleep, HRV, daily summaries)
        self.data_247 = None

        self.webhooks = WahooWebhookHandler(workouts=self.workouts)
        # No programmatic webhook registration API: the callback URL and shared
        # webhook_token are configured manually in the Wahoo developer portal.
        # self.webhook_service stays the BaseProviderStrategy default (None).

    @property
    def name(self) -> str:
        """Unique identifier for the provider (lowercase)."""
        return "wahoo"

    @property
    def api_base_url(self) -> str:
        """Base URL for the provider's API."""
        return "https://api.wahooligan.com"

    @property
    def coverage(self) -> ProviderCoverage:
        return ProviderCoverage(
            timeseries=TIMESERIES,
            workout_fields=WORKOUT_FIELDS,
            sleep_fields=SLEEP_FIELDS,
            health_scores=HEALTH_SCORES,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Historical sync uses REST (rest_pull). Live data optionally via webhook_stream:
        # Wahoo pushes the complete workout + workout_summary inline (not a ping),
        # configured manually in the developer portal (no webhook_registration_api).
        return ProviderCapabilities(rest_pull=True, webhook_stream=True)
