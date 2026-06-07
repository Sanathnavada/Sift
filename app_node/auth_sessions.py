"""
Spotify auth-session management for API-first OAuth flows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

from music_node.config.config import (
    SPOTIFY_CACHE_PATH,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuthSession:
    id: str
    status: str
    state: str
    authorization_url: str
    created_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    error: Optional[str] = None


class SpotifyAuthSessionManager:
    def __init__(self):
        self._sessions: dict[str, AuthSession] = {}
        self._state_to_session: dict[str, str] = {}

    @property
    def redirect_uri(self) -> str:
        return SPOTIFY_REDIRECT_URI

    def has_cached_user_token(self) -> bool:
        oauth = self._build_oauth()
        token_info = oauth.cache_handler.get_cached_token()
        return bool(token_info and oauth.validate_token(token_info))

    def start_session(self) -> AuthSession:
        session_id = str(uuid.uuid4())
        state = str(uuid.uuid4())
        oauth = self._build_oauth()
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

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        return self._sessions.get(session_id)

    def complete_from_code(self, session_id: str, code: str, state: Optional[str] = None) -> AuthSession:
        session = self._get_required_session(session_id)
        if state and state != session.state:
            raise ValueError("OAuth state mismatch.")

        oauth = self._build_oauth()
        oauth.get_access_token(code=code, check_cache=False)
        session.status = "authorized"
        session.finished_at = _now()
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

    def _get_required_session(self, session_id: str) -> AuthSession:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError("Auth session not found.")
        return session

    def _build_oauth(self) -> SpotifyOAuth:
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            raise ValueError("Spotify credentials missing in .env")

        handler = CacheFileHandler(cache_path=str(SPOTIFY_CACHE_PATH))
        return SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="playlist-read-private playlist-read-collaborative",
            open_browser=False,
            cache_handler=handler,
        )


spotify_auth_sessions = SpotifyAuthSessionManager()
