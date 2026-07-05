from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_instagram_popup_uses_fresh_window_name_per_attempt():
    js = (_root() / 'src/sift/app/web/static/js/app.js').read_text()
    assert 'var popupName = "instagram-auth-" + Date.now();' in js
    assert 'window.open(\n      popupUrl || "about:blank",\n      popupName,' in js
    assert '"instagram-auth",\n      "popup=yes,width="' not in js


def test_local_env_docker_does_not_force_novnc_for_direct_uvicorn():
    env = (_root() / '.env.docker').read_text()
    assert 'NOVNC_ENABLED=false' in env


def test_script_cache_buster_updated_for_popup_fix():
    base = (_root() / 'src/sift/app/web/templates/base.html').read_text()
    popup_page = (_root() / 'src/sift/app/web/templates/pages/instagram_auth_window.html').read_text()
    assert 'app.js?v=auth-runtime-fix-2' in base
    assert 'app.js?v=auth-runtime-fix-2' in popup_page


def test_novnc_auto_host_runtime_settings_present():
    settings = (_root() / 'src/sift/app/settings.py').read_text()
    routes = (_root() / 'src/sift/app/web/routes.py').read_text()
    compose = (_root() / 'docker-compose.yml').read_text()

    assert 'NOVNC_AUTO_HOST = _env_bool("NOVNC_AUTO_HOST", True)' in settings
    assert 'def _novnc_url_for_request(request: Request) -> str:' in routes
    assert 'request.url.hostname' in routes
    assert 'NOVNC_AUTO_HOST: "true"' in compose


def test_instagram_auth_errors_are_sanitized_for_ui():
    routes = (_root() / 'src/sift/app/web/routes.py').read_text()
    template = (_root() / 'src/sift/app/web/templates/partials/instagram_auth_panel.html').read_text()

    assert 'def _friendly_instagram_auth_error' in routes
    assert 'Target page, context or browser has been closed' not in template
    assert 'auth_error_message' in template
