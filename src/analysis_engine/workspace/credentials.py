import base64
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# The host each provider's clone URLs point at. The token is only ever sent
# to exactly this origin: git config's http.<url>.extraheader is scoped by
# URL, so a redirect to another host doesn't carry it.
_HOSTS = {"github": "https://github.com/", "gitlab": "https://gitlab.com/"}

# The username that goes with an OAuth token in HTTP Basic auth. GitLab
# documents "oauth2"; GitHub only reads the password field and uses the
# token from it, and "x-access-token" is the conventional username.
_USERNAMES = {"github": "x-access-token", "gitlab": "oauth2"}


def git_auth_env(provider: str, token: str, base_url: str | None = None) -> dict[str, str]:
    """
    Environment variables that make git send `token` to one host.

    Configuration through the environment (GIT_CONFIG_COUNT/KEY/VALUE,
    git ≥ 2.31) instead of the alternatives, each of which leaks it:
      - a token in the clone URL is saved in .git/config, which sits on
        disk in the workspace;
      - `git -c http.extraheader=...` puts it on the command line, readable
        by every user on the host through `ps`.
    A process's environment is readable only by its own user (and root).
    """
    host = base_url or _HOSTS[provider]
    basic = base64.b64encode(f"{_USERNAMES.get(provider, 'x-access-token')}:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{host}.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


class RepositoryCredentials:
    """
    Asks integration-service for a token to clone a repository with
    (GET /internal/repository-token, authenticated with the shared
    INTERNAL_SERVICE_TOKEN). The token is the OAuth token of a user who
    connected the repository, refreshed by integration-service when needed.

    Returns None — clone anonymously — when the repository isn't connected
    or integration-service can't be reached. A public repository still
    clones; a private one fails at clone time with git's own error, which
    is the honest outcome.

    Never logs a token, a response body, or an exception that could hold
    one.
    """

    def __init__(self, base_url: str, internal_token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._internal_token = internal_token
        self._transport = transport

    async def token_for(self, provider: str, repository: str) -> str | None:
        if not self._internal_token:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.get(
                    f"{self._base_url}/internal/repository-token",
                    params={"provider": provider, "repository": repository},
                    headers={"Authorization": f"Bearer {self._internal_token}"},
                )
        except httpx.HTTPError as error:
            logger.warning("could not reach integration-service for %s credentials (%s); cloning anonymously",
                           repository, type(error).__name__)
            return None

        if response.status_code == 404:
            return None  # not connected: fine for a public repository
        if response.status_code != 200:
            logger.warning("integration-service answered %d for %s credentials; cloning anonymously",
                           response.status_code, repository)
            return None
        try:
            token = response.json()["data"]["token"]
        except (ValueError, KeyError, TypeError):
            logger.warning("malformed credentials response for %s; cloning anonymously", repository)
            return None
        return token if isinstance(token, str) and token else None


def default_credentials() -> RepositoryCredentials | None:
    token = settings.internal_service_token.get_secret_value() if settings.internal_service_token else ""
    return RepositoryCredentials(settings.integration_service_url, token) if token else None
