from fastapi.testclient import TestClient

from sift.app.main import app, create_app


def test_phase3_app_factory_registers_existing_public_routes():
    fresh_app = create_app()
    route_paths = {getattr(route, "path", None) for route in fresh_app.routes}

    assert "/health" in route_paths
    assert "/callback" in route_paths
    assert "/api/tasks" in route_paths
    assert "/api/tasks/{task_id}" in route_paths
    assert "/api/tasks/{task_id}/artifacts" in route_paths
    assert "/static" in route_paths


def test_health_endpoint_still_returns_ok_after_main_split():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_task_contract_stays_404_after_tasks_router_split():
    client = TestClient(app)

    response = client.get("/api/tasks/not-a-real-task")

    assert response.status_code == 404
    assert "not-a-real-task" in response.json()["detail"]
