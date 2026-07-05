from pathlib import Path
from fastapi.testclient import TestClient

from sift.app.main import app


def test_core_server_rendered_pages_load_without_runtime_name_errors():
    client = TestClient(app)

    for path in ["/", "/media", "/music"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]


def test_ui_forms_and_tray_partials_load_after_web_route_split():
    client = TestClient(app)

    paths = [
        "/ui/system/status",
        "/ui/media/forms/youtube",
        "/ui/media/forms/instagram",
        "/ui/media/forms/post",
        "/ui/media/forms/public-user",
        "/ui/media/forms/private-user",
        "/ui/media/forms/bulk",
        "/ui/music/forms/song",
        "/ui/music/forms/youtube",
        "/ui/music/forms/spotify-library",
        "/ui/music/downloads/tray",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.text.strip(), path


def test_static_assets_still_served_from_migrated_package():
    client = TestClient(app)

    for path in ["/static/css/app.css", "/static/js/app.js"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert len(response.content) > 1000


def test_spotify_popup_close_watcher_is_present_in_static_js():
    js = Path('src/sift/app/web/static/js/app.js').read_text(encoding='utf-8')

    assert 'notifySpotifyAuthPopupClosed' in js
    assert 'data-spotify-auth-close-url' in Path('src/sift/app/web/templates/partials/spotify_auth_card.html').read_text(encoding='utf-8')
