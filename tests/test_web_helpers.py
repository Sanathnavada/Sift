from starlette.datastructures import FormData

from sift.app.runtime.input_resolver import InputResolutionError
from sift.app.runtime.tasks import Task
from sift.app.web.form_parsers import (
    _build_media_bulk_inputs,
    _build_music_song_inputs,
)
from sift.app.web.view_models import _task_view_model


def test_media_bulk_form_parser_extracts_instagram_urls_from_pasted_text():
    form = FormData({
        "urls_text": "watch these https://www.instagram.com/reel/ABC_123/?utm_source=test and https://instagram.com/p/xyz-789/",
    })

    assert _build_media_bulk_inputs(form) == [
        "https://www.instagram.com/reel/ABC_123/?utm_source=test",
        "https://instagram.com/p/xyz-789/",
    ]


def test_music_song_form_parser_requires_at_least_one_input():
    form = FormData({"query": "", "queries_text": ""})

    try:
        _build_music_song_inputs(form)
    except InputResolutionError as exc:
        assert "Provide at least one song" in str(exc)
    else:
        raise AssertionError("Expected InputResolutionError for empty music song form")


def test_task_view_model_preserves_task_card_contract_for_completed_task():
    task = Task(
        id="task-1",
        service="music.song",
        status="completed",
        submitted_at="2026-07-05T09:00:00+00:00",
        started_at="2026-07-05T09:00:10+00:00",
        finished_at="2026-07-05T09:01:15+00:00",
        result={
            "input": {"inputs": ["one", "two"]},
            "items": [{"title": "first"}, {"title": "second"}],
        },
        meta={
            "workflow_label": "Music search",
            "item_label": "Song",
            "estimated_total_label": "2m",
        },
    )

    model = _task_view_model(task, container_id="music-task-panel")

    assert model["task"] is task
    assert model["display_name"] == "Music search"
    assert model["display_status"] == "completed"
    assert model["display_status_variant"] == "success"
    assert model["task_card_url"] == "/ui/tasks/task-1/card?container_id=music-task-panel"
    assert model["task_page_url"] == "/tasks/task-1"
    assert model["artifact_list_url"] == "/ui/tasks/task-1/artifacts"
    assert model["duration_label"] == "1m 05s"
    assert model["estimate_label"] == "2m"
    assert model["result_items"] == [{"title": "first"}, {"title": "second"}]
