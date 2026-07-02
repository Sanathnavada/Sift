"""In-memory UI workspace state for restoring page panels during a browser session.

This module intentionally stores only lightweight UI pointers, mainly active
modes and task ids. The real task data remains in app_node.tasks. If the server
restarts or a task is cleaned up, stale ids are simply ignored by the renderer.
"""
from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Optional


_DEFAULT_STATE: dict[str, Any] = {
    "music": {
        "active_form": "download",
        "task_panel_task_id": None,
        "library_auth_session_id": None,
        "library_panel_task_id": None,
        "library_task_panel_task_id": None,
    },
    "media": {
        "active_form": "youtube",
        "youtube_task_id": None,
        "instagram_active_mode": "posts",
        "instagram_posts_task_id": None,
        "instagram_public_profile_task_id": None,
        "instagram_private_collection_task_id": None,
    },
}

_STATE: dict[str, dict[str, Any]] = {}
_LOCK = RLock()

_VALID_MUSIC_FORMS = {"download", "spotify-library"}
_VALID_MEDIA_FORMS = {"youtube", "instagram"}
_VALID_INSTAGRAM_MODES = {"posts", "public_profile", "private_collection"}


def _new_state() -> dict[str, Any]:
    return deepcopy(_DEFAULT_STATE)


def _session_key(session_id: Optional[str]) -> str:
    return session_id or "anonymous"


def get_ui_state(session_id: Optional[str]) -> dict[str, Any]:
    """Return a copy of the UI state for this browser/session."""
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        return deepcopy(state)


def set_music_active_form(session_id: Optional[str], active_form: str) -> None:
    if active_form not in _VALID_MUSIC_FORMS:
        return
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["music"]["active_form"] = active_form


def remember_music_auth_session(session_id: Optional[str], auth_session_id: str) -> None:
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["music"]["library_auth_session_id"] = auth_session_id


def remember_music_task(session_id: Optional[str], panel: str, task_id: str) -> None:
    panel_map = {
        "task": "task_panel_task_id",
        "library": "library_panel_task_id",
        "library_task": "library_task_panel_task_id",
        "music-task-panel": "task_panel_task_id",
        "music-library-panel": "library_panel_task_id",
        "music-library-task-panel": "library_task_panel_task_id",
    }
    key_name = panel_map.get(panel)
    if not key_name:
        return
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["music"][key_name] = task_id


def set_media_active_form(session_id: Optional[str], active_form: str) -> None:
    if active_form not in _VALID_MEDIA_FORMS:
        return
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["media"]["active_form"] = active_form


def set_instagram_active_mode(session_id: Optional[str], mode: str) -> None:
    if mode not in _VALID_INSTAGRAM_MODES:
        return
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["media"]["active_form"] = "instagram"
        state["media"]["instagram_active_mode"] = mode


def remember_media_task(session_id: Optional[str], slot: str, task_id: str) -> None:
    slot_map = {
        "youtube": "youtube_task_id",
        "media-youtube-task-panel": "youtube_task_id",
        "instagram_posts": "instagram_posts_task_id",
        "media-instagram-posts-task-panel": "instagram_posts_task_id",
        "instagram_public_profile": "instagram_public_profile_task_id",
        "media-instagram-public-profile-task-panel": "instagram_public_profile_task_id",
        "instagram_private_collection": "instagram_private_collection_task_id",
        "media-instagram-private-collection-task-panel": "instagram_private_collection_task_id",
        # Legacy generic media panel submissions are still useful to restore as YouTube/default.
        "media-task-panel": "youtube_task_id",
    }
    key_name = slot_map.get(slot)
    if not key_name:
        return
    key = _session_key(session_id)
    with _LOCK:
        state = _STATE.setdefault(key, _new_state())
        state["media"][key_name] = task_id
        if key_name == "youtube_task_id":
            state["media"]["active_form"] = "youtube"
        elif key_name.startswith("instagram_"):
            state["media"]["active_form"] = "instagram"
            if key_name == "instagram_posts_task_id":
                state["media"]["instagram_active_mode"] = "posts"
            elif key_name == "instagram_public_profile_task_id":
                state["media"]["instagram_active_mode"] = "public_profile"
            elif key_name == "instagram_private_collection_task_id":
                state["media"]["instagram_active_mode"] = "private_collection"
