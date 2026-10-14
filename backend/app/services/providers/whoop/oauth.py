import logging

import httpx

from app.config import settings
from app.schemas.auth import (
    AuthenticationMethod,
)
from app.schemas.enums import ProviderName
from app.schemas.model_crud.credentials import (
    OAuthTokenResponse,
    ProviderCredentials,
    ProviderEndpoints,
)
from app.services.providers.templates.base_oauth import BaseOAuthTemplate
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)


class WhoopOAuth(BaseOAuthTemplate):
    """Whoop OAuth 2.0 implementation."""

    @property
    def endpoints(self) -> ProviderEndpoints:
        """OAuth endpoints for authorization and token exchange."""
        return ProviderEndpoints(
            authorize_url="https://api.prod.whoop.com/oauth/oauth2/auth",
            token_url="https://api.prod.whoop.com/oauth/oauth2/token",
        )

    @property
    def credentials(self) -> ProviderCredentials:
        """OAuth credentials from environment variables."""
        return ProviderCredentials(
            client_id=settings.whoop_client_id or "",
            client_secret=(settings.whoop_client_secret.get_secret_value() if settings.whoop_client_secret else ""),
            redirect_uri=settings.oauth_redirect_uri(ProviderName.WHOOP),
            default_scope=settings.whoop_default_scope,
        )

    # OAuth configuration
    use_pkce: bool = False  # Whoop doesn't require PKCE
    auth_method: AuthenticationMethod = AuthenticationMethod.BODY  # Based on Whoop API docs, credentials in body

    def _get_provider_user_info(self, token_response: OAuthTokenResponse, user_id: str) -> dict[str, str | None]:
        """Returns provider user info without touching Whoop PII.

        We deliberately do NOT request the ``read:profile`` scope and do NOT call
        ``/v2/user/profile/basic`` — that endpoint returns the member's name and email,
        which we must not collect (see the privacy policy). The provider_user_id it used
        to supply is not needed: Whoop sync is pull-based, keyed by our internal user_id
        via the stored tokens, and no Whoop webhook routes by provider_user_id.
        """
        log_structured(
            logger,
            "info",
            "Skipping Whoop profile fetch (read:profile not requested; no PII collected)",
            provider="whoop",
            task="get_provider_user_info",
            user_id=user_id,
        )
        return {"user_id": None, "username": None}

    def deregister_user(self, access_token: str, provider_user_id: str | None = None) -> None:
        """Revoke WHOOP OAuth access for this user."""

        response = httpx.delete(
            f"{self.api_base_url}/v2/user/access",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        response.raise_for_status()
