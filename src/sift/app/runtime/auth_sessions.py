"""
Spotify auth-session management for API-first OAuth flows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sift.engines.music.config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_USER_CACHE_PATH,
)


_PLACEHOLDER_VALUES = {"", "replace-me", "changeme", "change-me", "todo", "none", "null"}


class SpotifyAuthConfigurationError(ValueError):
    """Raised when Spotify OAuth cannot start because credentials are not configured."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_secret(value: Optional[str]) -> str:
    return (value or "").strip()


def _is_configured_secret(value: Optional[str]) -> bool:
    return _normalized_secret(value).lower() not in _PLACEHOLDER_VALUES


@dataclass
class AuthSession:
    id: str
    status: str
    state: str
    authorization_url: str
    created_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    error: Optional[str] = None
    user_id: Optional[str] = None
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None


class SpotifyAuthSessionManager:
    def __init__(self):
        self._sessions: dict[str, AuthSession] = {}
        self._state_to_session: dict[str, str] = {}

    @property
    def redirect_uri(self) -> str:
        return SPOTIFY_REDIRECT_URI

    def has_configured_credentials(self) -> bool:
        return _is_configured_secret(SPOTIFY_CLIENT_ID) and _is_configured_secret(SPOTIFY_CLIENT_SECRET)

    def clear_cached_user_token(self) -> None:
        """Remove the user OAuth cache without touching app/client-credential cache.

        Starting a new UI approval flow should not silently rely on an older
        user's cached token. The Spotify account still comes from the account
        currently logged in inside the browser/approval popup.
        """
        try:
            SPOTIFY_USER_CACHE_PATH.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def has_cached_user_token(self) -> bool:
        return self.cached_user_profile() is not None

    def cached_user_profile(self) -> Optional[dict]:
        try:
            oauth = self._build_oauth(show_dialog=False)
        except SpotifyAuthConfigurationError:
            return None
        token_info = oauth.cache_handler.get_cached_token()
        if not token_info or not oauth.validate_token(token_info):
            return None
        try:
            return self._profile_for_oauth(oauth)
        except Exception:
            return None

    def start_session(self) -> AuthSession:
        # Make every explicit UI approval attempt a fresh user-authorization
        # attempt. This avoids the app looking "already connected" because an
        # older user token is still present in the local cache.
        self.clear_cached_user_token()
        session_id = str(uuid.uuid4())
        state = str(uuid.uuid4())
        oauth = self._build_oauth(show_dialog=True)
        authorization_url = oauth.get_authorize_url(state=state)
        session = AuthSession(
            id=session_id,
            status="waiting_for_user",
            state=state,
            authorization_url=authorization_url,
        )
        self._sessions[session_id] = session
        self._state_to_session[state] = session_id
        return session

    def start_configuration_error_session(self, error: str) -> AuthSession:
        """Create a failed session that can be rendered by the existing auth card."""
        session_id = str(uuid.uuid4())
        session = AuthSession(
            id=session_id,
            status="failed",
            state="",
            authorization_url="",
            finished_at=_now(),
            error=error,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        return self._sessions.get(session_id)

    def complete_from_code(self, session_id: str, code: str, state: Optional[str] = None) -> AuthSession:
        session = self._get_required_session(session_id)
        if state and state != session.state:
            raise ValueError("OAuth state mismatch.")

        oauth = self._build_oauth(show_dialog=False)
        oauth.get_access_token(code=code, check_cache=False)
        profile = self._profile_for_oauth(oauth)
        session.status = "authorized"
        session.finished_at = _now()
        session.user_id = profile.get("id")
        session.user_display_name = profile.get("display_name") or profile.get("id")
        session.user_email = profile.get("email")
        return session

    def complete_from_callback_state(self, state: str, code: str) -> AuthSession:
        session_id = self._state_to_session.get(state)
        if not session_id:
            raise ValueError("Unknown Spotify auth session state.")
        return self.complete_from_code(session_id, code, state=state)

    def fail_from_callback_state(self, state: str, error: str) -> AuthSession:
        session_id = self._state_to_session.get(state)
        if not session_id:
            raise ValueError("Unknown Spotify auth session state.")
        return self.fail_session(session_id, error)

    def fail_session(self, session_id: str, error: str) -> AuthSession:
        session = self._get_required_session(session_id)
        session.status = "failed"
        session.error = error
        session.finished_at = _now()
        return session

    def abandon_session(self, session_id: str, error: str) -> AuthSession:
        """Mark a still-pending browser OAuth attempt as failed."""
        session = self._get_required_session(session_id)
        if session.status == "waiting_for_user":
            session.status = "failed"
            session.error = error
            session.finished_at = _now()
        return session

    def _get_required_session(self, session_id: str) -> AuthSession:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError("Auth session not found.")
        return session

    def _build_oauth(self, *, show_dialog: bool = False):
        if not self.has_configured_credentials():
            raise SpotifyAuthConfigurationError(
                "Spotify credentials are not configured. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET before starting library authorization."
            )

        from spotipy.cache_handler import CacheFileHandler
        from spotipy.oauth2 import SpotifyOAuth

        handler = CacheFileHandler(cache_path=str(SPOTIFY_USER_CACHE_PATH))
        return SpotifyOAuth(
            client_id=_normalized_secret(SPOTIFY_CLIENT_ID),
            client_secret=_normalized_secret(SPOTIFY_CLIENT_SECRET),
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="playlist-read-private playlist-read-collaborative",
            open_browser=False,
            cache_handler=handler,
            show_dialog=show_dialog,
        )

    @staticmethod
    def _profile_for_oauth(oauth) -> dict:
        import spotipy

        profile = spotipy.Spotify(auth_manager=oauth).current_user()
        if not profile or not profile.get("id"):
            raise ValueError("Spotify authorization did not return a user profile.")
        return profile


spotify_auth_sessions = SpotifyAuthSessionManager()
