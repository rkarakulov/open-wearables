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

# hard-coded value - update with base template changes
_WAHOO_USER_ENDPOINT = "/v1/user"
# Wahoo has no POST /oauth/revoke; DELETE /v1/permissions revokes the grant (Bearer-authenticated).
_WAHOO_PERMISSIONS_ENDPOINT = "/v1/permissions"


class WahooOAuth(BaseOAuthTemplate):
    """Wahoo OAuth 2.0 implementation.

    Authorization Code + PKCE (S256); credentials are still sent in the token
    request body (not Basic Auth), matching Wahoo's documented token endpoint.
    """

    @property
    def endpoints(self) -> ProviderEndpoints:
        """OAuth endpoints for authorization and token exchange."""
        return ProviderEndpoints(
            authorize_url=f"{self.api_base_url}/oauth/authorize",
            token_url=f"{self.api_base_url}/oauth/token",
        )

    @property
    def credentials(self) -> ProviderCredentials:
        """OAuth credentials from environment variables."""
        return ProviderCredentials(
            client_id=settings.wahoo_client_id or "",
            client_secret=(settings.wahoo_client_secret.get_secret_value() if settings.wahoo_client_secret else ""),
            redirect_uri=settings.oauth_redirect_uri(ProviderName.WAHOO),
            default_scope=settings.wahoo_default_scope,
        )

    # OAuth configuration
    use_pkce: bool = True  # Wahoo requires PKCE (S256) on top of the standard authorization code grant
    auth_method: AuthenticationMethod = AuthenticationMethod.BODY  # Wahoo expects credentials in the token body

    def deregister_user(self, access_token: str, provider_user_id: str | None = None) -> None:
        """Revoke the athlete's grant via DELETE /v1/permissions (Bearer-authenticated, idempotent)."""
        response = httpx.delete(
            f"{self.api_base_url}{_WAHOO_PERMISSIONS_ENDPOINT}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        # An already-revoked/expired token still counts as "deregistered".
        if response.status_code not in (200, 204, 401, 403, 404):
            response.raise_for_status()

    def _get_provider_user_info(self, token_response: OAuthTokenResponse, user_id: str) -> dict[str, str | None]:
        """Fetches the Wahoo user id via API. Wahoo has no public username field."""
        try:
            response = httpx.get(
                f"{self.api_base_url}{_WAHOO_USER_ENDPOINT}",
                headers={"Authorization": f"Bearer {token_response.access_token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            user_data = response.json()
            provider_user_id = user_data.get("id")
            provider_user_id = str(provider_user_id) if provider_user_id is not None else None
            return {"user_id": provider_user_id, "username": None}
        except Exception:
            return {"user_id": None, "username": None}
