from fastapi.testclient import TestClient

from sift.app.main import app
from sift.app.runtime.auth_sessions import AuthSession, spotify_auth_sessions


def _install_test_session(session: AuthSession):
    spotify_auth_sessions._sessions[session.id] = session
    spotify_auth_sessions._state_to_session[session.state] = session.id


def _remove_test_session(session: AuthSession):
    spotify_auth_sessions._sessions.pop(session.id, None)
    spotify_auth_sessions._state_to_session.pop(session.state, None)


def test_abandon_waiting_spotify_session_marks_it_failed():
    session = AuthSession(
        id="test-abandon-waiting",
        status="waiting_for_user",
        state="state-abandon-waiting",
        authorization_url="https://accounts.spotify.com/authorize?client_id=replace-me",
    )
    _install_test_session(session)
    try:
        result = spotify_auth_sessions.abandon_session(session.id, "window closed")

        assert result.status == "failed"
        assert result.error == "window closed"
        assert result.finished_at is not None
    finally:
        _remove_test_session(session)


def test_abandon_spotify_session_does_not_regress_authorized_callback_race():
    session = AuthSession(
        id="test-abandon-authorized",
        status="authorized",
        state="state-abandon-authorized",
        authorization_url="https://accounts.spotify.com/authorize",
        finished_at="already-finished",
    )
    _install_test_session(session)
    try:
        result = spotify_auth_sessions.abandon_session(session.id, "window closed")

        assert result.status == "authorized"
        assert result.error is None
        assert result.finished_at == "already-finished"
    finally:
        _remove_test_session(session)


def test_ui_popup_close_endpoint_refreshes_waiting_auth_card_to_retry_state():
    session = AuthSession(
        id="test-ui-popup-close",
        status="waiting_for_user",
        state="state-ui-popup-close",
        authorization_url="https://accounts.spotify.com/authorize?client_id=replace-me",
    )
    _install_test_session(session)
    client = TestClient(app)
    try:
        response = client.post(f"/ui/music/auth/session/{session.id}/closed")

        assert response.status_code == 200
        assert "Library connection failed" in response.text
        assert "Spotify approval window was closed before authorization completed." in response.text
        assert "Try Again" in response.text
        assert spotify_auth_sessions.get_session(session.id).status == "failed"
    finally:
        _remove_test_session(session)


def test_spotify_auth_card_wires_popup_close_refresh_url():
    session = AuthSession(
        id="test-card-close-url",
        status="waiting_for_user",
        state="state-card-close-url",
        authorization_url="https://accounts.spotify.com/authorize?client_id=replace-me",
    )
    _install_test_session(session)
    client = TestClient(app)
    try:
        response = client.get(f"/ui/music/auth/session/{session.id}/card")

        assert response.status_code == 200
        assert 'data-spotify-auth-close-url="/ui/music/auth/session/test-card-close-url/closed"' in response.text
        assert 'data-spotify-auth-poll-url="/ui/music/auth/session/test-card-close-url/card"' in response.text
    finally:
        _remove_test_session(session)


def test_placeholder_spotify_credentials_are_treated_as_unconfigured(monkeypatch):
    from sift.app.runtime import auth_sessions as auth_module

    monkeypatch.setattr(auth_module, "SPOTIFY_CLIENT_ID", "replace-me")
    monkeypatch.setattr(auth_module, "SPOTIFY_CLIENT_SECRET", "replace-me")
    manager = auth_module.SpotifyAuthSessionManager()

    assert manager.has_configured_credentials() is False
    try:
        manager.start_session()
    except auth_module.SpotifyAuthConfigurationError as exc:
        assert "Spotify credentials are not configured" in str(exc)
    else:
        raise AssertionError("placeholder credentials should not open Spotify auth")


def test_ui_auth_start_with_placeholder_credentials_shows_failed_card_without_auth_link(monkeypatch):
    from sift.app.runtime import auth_sessions as auth_module

    monkeypatch.setattr(auth_module, "SPOTIFY_CLIENT_ID", "replace-me")
    monkeypatch.setattr(auth_module, "SPOTIFY_CLIENT_SECRET", "replace-me")

    client = TestClient(app)
    response = client.post("/ui/music/user/auth/start")

    assert response.status_code == 200
    assert "Library connection failed" in response.text
    assert "Spotify credentials are not configured" in response.text
    assert "accounts.spotify.com/authorize" not in response.text
    assert "Open approval window" not in response.text


def test_spotify_auth_popup_uses_fresh_window_name_and_not_fixed_stale_window():
    from pathlib import Path

    js = Path('src/sift/app/web/static/js/app.js').read_text(encoding='utf-8')

    assert 'var popupName = "spotify-auth-" + Date.now();' in js
    assert 'window.open(toSameOriginAbsoluteUrl(link.href), popupName' in js
    assert 'window.open(link.href, "spotify-auth"' not in js


def test_spotify_callback_no_longer_redirects_popup_to_music_page():
    from pathlib import Path

    music_route = Path('src/sift/app/api/routes/music.py').read_text(encoding='utf-8')

    assert "window.location.href = '/music'" not in music_route
    assert "You can close this window and return to Sift." in music_route


def test_spotify_auth_sessions_use_user_cache_and_force_dialog():
    from pathlib import Path

    auth_code = Path('src/sift/app/runtime/auth_sessions.py').read_text(encoding='utf-8')
    spotify_code = Path('src/sift/engines/music/services/spotify.py').read_text(encoding='utf-8')
    config_code = Path('src/sift/engines/music/config.py').read_text(encoding='utf-8')

    assert 'SPOTIFY_USER_CACHE_PATH = STATE_DIR / ".spotify_user_cache"' in config_code
    assert 'SPOTIFY_CLIENT_CACHE_PATH = STATE_DIR / ".spotify_client_cache"' in config_code
    assert 'self.clear_cached_user_token()' in auth_code
    assert 'show_dialog=True' in auth_code
    assert 'current_user()' in auth_code
    assert 'SPOTIFY_CLIENT_CACHE_PATH' in spotify_code
    assert 'SPOTIFY_USER_CACHE_PATH' in spotify_code


def test_authorized_spotify_card_shows_connected_user_identity():
    session = AuthSession(
        id="test-authorized-user-label",
        status="authorized",
        state="state-authorized-user-label",
        authorization_url="https://accounts.spotify.com/authorize",
        finished_at="done",
        user_id="spotify_user_123",
        user_display_name="Test Listener",
    )
    _install_test_session(session)
    client = TestClient(app)
    try:
        response = client.get(f"/ui/music/auth/session/{session.id}/card")
        assert response.status_code == 200
        assert "Connected as Test Listener" in response.text
        assert "spotify_user_123" in response.text
    finally:
        _remove_test_session(session)


def test_spotify_callback_fallback_page_uses_app_styling_and_escapes_values():
    from sift.app.api.routes.music import _spotify_callback_html

    session = AuthSession(
        id="styled-callback-session",
        status="authorized",
        state="styled-callback-state",
        authorization_url="https://accounts.spotify.com/authorize",
        user_id="spotify_user_123",
        user_display_name="Sanath <Listener>",
    )

    response = _spotify_callback_html(session, "Spotify authorization complete.")
    body = response.body.decode("utf-8")

    assert "/static/css/app.css" in body
    assert "auth-callback-shell" in body
    assert "task-card auth-callback-card" in body
    assert "status-pill status-pill--success" in body
    assert "Sanath &lt;Listener&gt;" in body
    assert "Sanath <Listener>" not in body
    assert "<html><body>" not in body
