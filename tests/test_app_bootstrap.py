from sift.app.main import app
from sift.app.settings import STATIC_DIR, TEMPLATES_DIR


def test_fastapi_app_imports_after_src_migration():
    assert app.title == "Node Services API"
    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in route_paths
    assert "/api/tasks" in route_paths


def test_server_rendered_ui_assets_are_inside_app_web_package():
    assert TEMPLATES_DIR.exists()
    assert (TEMPLATES_DIR / "base.html").exists()
    assert STATIC_DIR.exists()
    assert (STATIC_DIR / "css" / "app.css").exists()
