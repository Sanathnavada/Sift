"""
Server-rendered UI routes and HTMX partials for Gateway Console.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from typing import Optional

from markupsafe import Markup
from urllib.parse import parse_qs, urlparse, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..runtime.auth_sessions import SpotifyAuthConfigurationError, spotify_auth_sessions
from ..runtime.input_resolver import InputResolutionError, resolve_multi_input
from ..runtime.music_download_tray import collect_music_downloads
from ..runtime.ui_session_state import (
    get_ui_state,
    remember_media_task,
    remember_music_auth_session,
    remember_music_task,
    set_instagram_active_mode,
    set_media_active_form,
    set_music_active_form,
)
from ..runtime.instagram_sessions import SESSION_HEADER
from ..settings import (
    INSTAGRAM_AUTH_BROWSER_ENABLED,
    MEDIA_NODE_ENABLED,
    MUSIC_NODE_ENABLED,
    NAVIDROME_ENABLED,
    NOVNC_AUTO_HOST,
    NOVNC_ENABLED,
    NOVNC_PORT,
    NOVNC_PUBLIC_URL,
    TEMPLATES_DIR,
    TELEGRAM_NODE_ENABLED,
)
from ..runtime.tasks import (
    all_tasks,
    cancel_task,
    get_queue_summary,
    get_task,
    submit_async_job,
    submit_bound_job,
)
from ..api.routes import media as media_api
from ..api.routes import music as music_api
from ..api.routes import telegram as telegram_api
from .form_parsers import (
    _build_media_bulk_inputs,
    _build_media_youtube_inputs,
    _build_music_link_inputs,
    _build_music_song_inputs,
    _build_music_yt_inputs,
)
from .view_models import _task_display_name, _task_summary_cards, _task_view_model


router = APIRouter(tags=["UI"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
INSTAGRAM_AUTH_TASK_COOKIE = "gateway_instagram_auth_task_id"
UI_SESSION_COOKIE = "gateway_client_session_id"
GITHUB_URL = "https://github.com/Sanathnavada/Code"


def _format_host_port(host: str, port: int | None) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def _novnc_url_for_request(request: Request) -> str:
    """Resolve the iframe URL for the browser viewing this FastAPI app."""
    configured = NOVNC_PUBLIC_URL
    if not NOVNC_AUTO_HOST:
        return configured

    parts = urlsplit(configured)
    request_host = request.url.hostname
    if not parts.scheme or not parts.netloc or not request_host:
        return configured

    configured_host = parts.hostname or ""
    if configured_host.lower() not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return configured

    port = parts.port or NOVNC_PORT
    netloc = _format_host_port(request_host, port)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _friendly_instagram_auth_error(error: str | None) -> str:
    if not error:
        return "Instagram login did not complete."
    normalized = " ".join(str(error).split())
    lower = normalized.lower()
    if "target page, context or browser has been closed" in lower:
        return "The Instagram login window was closed before login completed. Start a fresh login when you are ready."
    if "waiting for locator" in lower or "wait_for_selector" in lower:
        return "Instagram login did not finish before the login page was ready. Open the login window again and complete the login there."
    if "timeout" in lower:
        return "Instagram login timed out before the session was saved. Open the login window again and complete the login or two-factor step."
    if len(normalized) > 220:
        return normalized[:217].rstrip() + "..."
    return normalized

def _feature_flags() -> dict[str, bool]:
    return {
        "telegram": TELEGRAM_NODE_ENABLED,
        "media": MEDIA_NODE_ENABLED,
        "music": MUSIC_NODE_ENABLED,
        "navidrome": NAVIDROME_ENABLED,
    }



def _template_context(request: Request, active_page: str = "", **context) -> dict:
    base_context = {
        "request": request,
        "active_page": active_page,
        "github_url": GITHUB_URL,
        "feature_flags": _feature_flags(),
    }
    base_context.update(context)
    return base_context


def _render_fragment(request: Request, template_name: str, **context) -> Markup:
    template = templates.env.get_template(template_name)
    return Markup(template.render(_template_context(request, **context)))

def _attach_ui_session_cookie(request: Request, response):
    session_id = request.headers.get(SESSION_HEADER) or request.cookies.get(UI_SESSION_COOKIE)
    if not session_id:
        session_id = uuid4().hex
    response.set_cookie(
        UI_SESSION_COOKIE,
        session_id,
        max_age=60 * 60 * 24 * 7,
        path="/",
        samesite="lax",
        httponly=False,
    )
    return response


def _render(request: Request, template_name: str, **context):
    base_context = {
        "request": request,
        "active_page": context.pop("active_page", ""),
        "github_url": GITHUB_URL,
        "feature_flags": _feature_flags(),
    }
    base_context.update(context)
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=base_context,
    )
    return _attach_ui_session_cookie(request, response)


def _request_client_id(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _ui_session_id(request: Request) -> Optional[str]:
    return (
        request.headers.get(SESSION_HEADER)
        or request.cookies.get(UI_SESSION_COOKIE)
        or _request_client_id(request)
    )


def _music_session_id(request: Request) -> Optional[str]:
    return _ui_session_id(request)


def _music_task_meta(request: Request, workflow_label: str, item_label: str, item_detail: str, **extra) -> dict:
    return _job_meta(
        workflow_label,
        item_label,
        item_detail,
        music_session_id=_music_session_id(request),
        music_client_id=_request_client_id(request),
        **extra,
    )


def _music_download_tray_context(request: Request, *, oob: bool = False) -> dict:
    return {
        "music_downloads": collect_music_downloads(
            _music_session_id(request),
            client_id=_request_client_id(request),
        ),
        "music_download_tray_oob": oob,
    }


def _media_meta(workflow_label: str, item_label: str, item_detail: str, **extra) -> dict:
    return {
        "workflow_label": workflow_label,
        "item_label": item_label,
        "item_detail": item_detail,
        **extra,
    }


def _job_meta(workflow_label: str, item_label: str, item_detail: str, **extra) -> dict:
    return _media_meta(workflow_label, item_label, item_detail, **extra)


def _render_error_panel(request: Request, message: str, title: str = "Request Error",
                        status_code: int = status.HTTP_400_BAD_REQUEST):
    response = _render(
        request,
        "partials/flash_panel.html",
        message=message,
        title=title,
        kind="error",
    )
    response.status_code = status_code
    return response


def _session_dir_from_request_or_task(request: Request, task=None):
    session_id = request.headers.get(SESSION_HEADER)
    if not session_id and task is not None:
        session_id = task.meta.get("client_session_id")
    return media_api._session_dir(session_id)




def _active_instagram_auth_tasks_for_session(client_session_id: Optional[str]) -> list:
    return [
        task for task in all_tasks()
        if task.service == "media.instagram_auth"
        and task.status in {"queued", "running"}
        and task.meta.get("client_session_id") == client_session_id
    ]


async def _stop_active_instagram_auth_for_session(client_session_id: Optional[str], session_dir) -> None:
    for task in _active_instagram_auth_tasks_for_session(client_session_id):
        media_api._request_instagram_auth_cancel(session_dir, task.id)
        await cancel_task(task.id)

def _render_instagram_auth_panel(
    request: Request,
    *,
    auth_task_id: Optional[str] = None,
    open_popup: bool = False,
    status_checked: bool = False,
    reset_done: bool = False,
):
    task = get_task(auth_task_id) if auth_task_id else None
    session_dir = _session_dir_from_request_or_task(request, task)
    auth_status = media_api._instagram_auth_status(session_dir)

    # Keep the internal auth task id out of the visible popup URL.
    # The popup resolves the active auth task from this browser's short-lived cookie.
    auth_window_url = "/ui/media/instagram/auth/window" if auth_task_id else ""
    poll_url = f"/ui/media/instagram/auth/session/{auth_task_id}/card" if auth_task_id else ""
    close_url = "/ui/media/instagram/auth/session/close" if auth_task_id else ""

    return _render(
        request,
        "partials/instagram_auth_panel.html",
        auth_status=auth_status,
        auth_task=task,
        auth_task_id=auth_task_id,
        auth_window_url=auth_window_url,
        auth_poll_url=poll_url,
        auth_close_url=close_url,
        open_popup=open_popup,
        status_checked=status_checked,
        reset_done=reset_done,
        checked_at=datetime.now().astimezone().strftime("%I:%M:%S %p %Z").lstrip("0"),
        novnc_url=_novnc_url_for_request(request),
        novnc_enabled=NOVNC_ENABLED,
        auth_browser_enabled=INSTAGRAM_AUTH_BROWSER_ENABLED,
        auth_error_message=_friendly_instagram_auth_error(task.error if task else None),
    )


def _render_instagram_auth_required(request: Request, workflow_label: str):
    return _render_error_panel(
        request,
        f"Connect Instagram before running {workflow_label}.",
        title="Instagram Login Required",
        status_code=status.HTTP_409_CONFLICT,
    )


def _is_instagram_authenticated_for_request(request: Request) -> bool:
    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))
    return media_api._is_instagram_authenticated(session_dir)




def _empty_task_panel_html(request: Request, *, title: str, body: str) -> Markup:
    return _render_fragment(
        request,
        "partials/empty_task_panel.html",
        empty_title=title,
        empty_body=body,
    )


def _stale_or_empty_task_panel_html(request: Request, task_id: Optional[str], *, title: str, body: str) -> Markup:
    if not task_id:
        return _empty_task_panel_html(request, title=title, body=body)
    task = get_task(task_id)
    if not task:
        return _empty_task_panel_html(request, title=title, body=body)
    return _task_panel_html(request, task_id, title=title, container_id="task-panel")


def _task_panel_html(
    request: Request,
    task_id: Optional[str],
    *,
    title: str,
    container_id: str,
    include_music_tray_oob: bool = False,
) -> Markup:
    if not task_id:
        return Markup("")
    task = get_task(task_id)
    if not task:
        return _render_fragment(request, "partials/stale_task_card.html")
    view_model = _task_view_model(task, container_id=container_id)
    if include_music_tray_oob and task.service.startswith("music."):
        view_model.update(_music_download_tray_context(request, oob=True))
    if (
        task.service == "music.user_playlists"
        and task.status == "completed"
        and view_model["playlist_options"]
    ):
        view_model["playlist_target_id"] = "#music-library-task-panel"
        return _render_fragment(
            request,
            "partials/spotify_library_panel.html",
            title=title,
            **view_model,
        )
    return _render_fragment(
        request,
        "partials/task_card.html",
        title=title,
        **view_model,
    )


def _spotify_auth_panel_html(request: Request, auth_session_id: Optional[str]) -> Markup:
    if not auth_session_id:
        return _empty_task_panel_html(
            request,
            title="No library task yet",
            body="Connect your Spotify library to fetch playlists and tracks. Library tasks and review results will appear here.",
        )
    session = spotify_auth_sessions.get_session(auth_session_id)
    if not session:
        return _render_fragment(request, "partials/stale_auth_card.html")
    return _render_fragment(
        request,
        "partials/spotify_auth_card.html",
        session=session,
        poll_url=f"/ui/music/auth/session/{session.id}/card",
        redirect_uri=spotify_auth_sessions.redirect_uri,
    )


def _music_workspace_context(request: Request) -> dict:
    state = get_ui_state(_ui_session_id(request)).get("music", {})
    active_form = state.get("active_form") or "download"
    if active_form not in {"download", "spotify-library"}:
        active_form = "download"
    form_template = (
        "partials/music_spotify_auth_form.html"
        if active_form == "spotify-library"
        else "partials/music_song_form.html"
    )
    return {
        "music_active_form": active_form,
        "music_form_template": form_template,
        "music_task_panel_html": _task_panel_html(
            request,
            state.get("task_panel_task_id"),
            title="Music Task",
            container_id="music-task-panel",
        ) if state.get("task_panel_task_id") else _empty_task_panel_html(
            request,
            title="No music task yet",
            body="Submit a search, link, or batch. Sift will show match confidence, review steps, and outputs here.",
        ),
        "music_library_auth_panel_html": _spotify_auth_panel_html(
            request,
            state.get("library_auth_session_id"),
        ),
        "music_library_panel_html": _task_panel_html(
            request,
            state.get("library_panel_task_id"),
            title="Spotify Library Task",
            container_id="music-library-panel",
        ) if state.get("library_panel_task_id") else Markup(""),
        "music_library_task_panel_html": _task_panel_html(
            request,
            state.get("library_task_panel_task_id"),
            title="Music Task",
            container_id="music-library-task-panel",
        ) if state.get("library_task_panel_task_id") else Markup(""),
    }


def _media_workspace_context(request: Request) -> dict:
    state = get_ui_state(_ui_session_id(request)).get("media", {})
    active_form = state.get("active_form") or "youtube"
    if active_form not in {"youtube", "instagram"}:
        active_form = "youtube"
    instagram_mode = state.get("instagram_active_mode") or "posts"
    if instagram_mode not in {"posts", "public_profile", "private_collection"}:
        instagram_mode = "posts"
    form_template = (
        "partials/media_instagram_form.html"
        if active_form == "instagram"
        else "partials/media_youtube_form.html"
    )
    return {
        "media_active_form": active_form,
        "media_form_template": form_template,
        "instagram_active_mode": instagram_mode,
        "media_youtube_panel_html": _task_panel_html(
            request,
            state.get("youtube_task_id"),
            title="Media Task",
            container_id="media-youtube-task-panel",
        ) if state.get("youtube_task_id") else _empty_task_panel_html(
            request,
            title="No YouTube task yet",
            body="Paste a video URL to start transcription. Task progress and outputs will appear here.",
        ),
        "media_instagram_posts_panel_html": _task_panel_html(
            request,
            state.get("instagram_posts_task_id"),
            title="Media Task",
            container_id="media-instagram-posts-task-panel",
        ) if state.get("instagram_posts_task_id") else _empty_task_panel_html(
            request,
            title="No post task yet",
            body="Submit post, reel, or carousel URLs to start OCR/transcription processing.",
        ),
        "media_instagram_public_panel_html": _task_panel_html(
            request,
            state.get("instagram_public_profile_task_id"),
            title="Media Task",
            container_id="media-instagram-public-profile-task-panel",
        ) if state.get("instagram_public_profile_task_id") else _empty_task_panel_html(
            request,
            title="No public profile task yet",
            body="Submit a username and post count to process recent media.",
        ),
        "media_instagram_private_panel_html": _task_panel_html(
            request,
            state.get("instagram_private_collection_task_id"),
            title="Media Task",
            container_id="media-instagram-private-collection-task-panel",
        ) if state.get("instagram_private_collection_task_id") else _empty_task_panel_html(
            request,
            title="No private collection task yet",
            body="Submit a saved collection name after connecting Instagram.",
        ),
    }

def _render_task_card(request: Request, task_id: str, *, title: Optional[str] = None,
                      container_id: str = "task-panel"):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")
    view_model = _task_view_model(task, container_id=container_id)
    if task.service.startswith("music."):
        view_model.update(_music_download_tray_context(request, oob=True))
    if (
        task.service == "music.user_playlists"
        and task.status == "completed"
        and view_model["playlist_options"]
    ):
        view_model["playlist_target_id"] = "#music-library-task-panel"
        return _render(
            request,
            "partials/spotify_library_panel.html",
            title=title,
            **view_model,
        )
    return _render(
        request,
        "partials/task_card.html",
        title=title,
        **view_model,
    )


@router.get("/", response_class=HTMLResponse, name="ui_home")
async def home_page(request: Request):
    return _render(
        request,
        "pages/index.html",
        active_page="home",
        status_cards=_task_summary_cards(),
    )


@router.get("/media", response_class=HTMLResponse, name="ui_media")
async def media_page(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    return _render(
        request,
        "pages/media.html",
        active_page="media",
        **_media_workspace_context(request),
    )


@router.get("/music", response_class=HTMLResponse, name="ui_music")
async def music_page(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    return _render(
        request,
        "pages/music.html",
        active_page="music",
        **_music_workspace_context(request),
        **_music_download_tray_context(request),
    )


@router.get("/telegram", response_class=HTMLResponse, name="ui_telegram")
async def telegram_page(request: Request):
    if not TELEGRAM_NODE_ENABLED:
        raise HTTPException(404, "Telegram node is disabled.")
    return _render(
        request,
        "pages/telegram.html",
        active_page="telegram",
    )


@router.get("/tasks/{task_id}", response_class=HTMLResponse, name="ui_task_detail")
async def task_page(request: Request, task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")
    return _render(
        request,
        "pages/task.html",
        active_page="task",
        task_id=task_id,
        page_title=_task_display_name(task),
        service_name=_task_display_name(task),
    )


@router.get("/ui/system/status", response_class=HTMLResponse)
async def system_status_partial(request: Request):
    return _render(
        request,
        "partials/system_status.html",
        status_cards=_task_summary_cards(),
        queue_summary=get_queue_summary(),
    )


@router.get("/ui/media/forms/{workflow}", response_class=HTMLResponse)
async def media_form_partial(request: Request, workflow: str):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    if workflow == "youtube":
        set_media_active_form(_ui_session_id(request), "youtube")
    elif workflow in {"instagram", "post", "public-user", "private-user", "bulk"}:
        set_media_active_form(_ui_session_id(request), "instagram")
    template_map = {
        "youtube": "partials/media_youtube_form.html",
        "instagram": "partials/media_instagram_form.html",
        "post": "partials/media_post_form.html",
        "public-user": "partials/media_public_form.html",
        "private-user": "partials/media_private_form.html",
        "bulk": "partials/media_bulk_form.html",
    }
    template_name = template_map.get(workflow)
    if not template_name:
        raise HTTPException(404, "Unknown media workflow.")
    media_state = get_ui_state(_ui_session_id(request)).get("media", {})
    return _render(
        request,
        template_name,
        instagram_active_mode=media_state.get("instagram_active_mode", "posts"),
    )


@router.get("/ui/media/instagram/auth/panel", response_class=HTMLResponse)
async def instagram_auth_panel(request: Request, checked: bool = False):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    return _render_instagram_auth_panel(request, status_checked=checked)


@router.post("/ui/media/instagram/auth/start", response_class=HTMLResponse)
async def instagram_auth_start_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    if not INSTAGRAM_AUTH_BROWSER_ENABLED:
        return _render_error_panel(
            request,
            "Instagram browser authentication is disabled for this deployment.",
            title="Instagram Login Unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    client_session_id = request.headers.get(SESSION_HEADER)
    session_dir = media_api._session_dir(client_session_id)

    # A manually closed/reopened login popup should not leave an old headed
    # Chromium auth job alive in the shared VNC display. Stop any previous
    # auth attempt for this browser session before starting a fresh one.
    await _stop_active_instagram_auth_for_session(client_session_id, session_dir)
    media_api._clear_instagram_auth_cancel(session_dir, "")
    task = await submit_bound_job(
        "media.instagram_auth",
        lambda task: media_api._instagram_auth_job(task.id, session_dir),
        submitted_by=_request_client_id(request),
        meta=_media_meta(
            "Instagram connection",
            "Auth browser",
            "Approve login in the popup console"
            if NOVNC_ENABLED else
            "Approve login in the local Chromium window",
            client_session_id=client_session_id,
        ),
    )
    response = _render_instagram_auth_panel(
        request,
        auth_task_id=task.id,
        open_popup=True,
    )
    response.set_cookie(
        INSTAGRAM_AUTH_TASK_COOKIE,
        task.id,
        max_age=900,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/ui/media/instagram/auth/session/{auth_task_id}/card", response_class=HTMLResponse)
async def instagram_auth_session_card(request: Request, auth_task_id: str):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    task = get_task(auth_task_id)
    if not task:
        return _render(request, "partials/stale_task_card.html")
    return _render_instagram_auth_panel(request, auth_task_id=auth_task_id)


@router.get("/ui/media/instagram/auth/session/{auth_task_id}/state")
async def instagram_auth_session_state(request: Request, auth_task_id: str):
    task = get_task(auth_task_id)
    if not task:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"status": "stale", "authenticated": False, "should_close": False},
        )

    session_dir = _session_dir_from_request_or_task(request, task)
    auth_status = media_api._instagram_auth_status(session_dir)
    task_status = task.status
    should_close = bool(auth_status.get("authenticated") or task_status in {"completed", "failed", "cancelled"})
    return {
        "status": task_status,
        "authenticated": bool(auth_status.get("authenticated")),
        "username": auth_status.get("username"),
        "error": task.error,
        "should_close": should_close,
    }


@router.get("/ui/media/instagram/auth/window", response_class=HTMLResponse)
async def instagram_auth_window(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")

    auth_task_id = request.cookies.get(INSTAGRAM_AUTH_TASK_COOKIE)
    if not auth_task_id:
        raise HTTPException(404, "Instagram auth session not found.")

    task = get_task(auth_task_id)
    if not task:
        raise HTTPException(404, "Instagram auth session not found.")

    return _render(
        request,
        "pages/instagram_auth_window.html",
        active_page="media",
        auth_task=task,
        auth_task_id=auth_task_id,
        auth_state_url="/ui/media/instagram/auth/session/state",
        auth_close_url="/ui/media/instagram/auth/session/close",
        novnc_url=_novnc_url_for_request(request),
        novnc_cache_bust=datetime.now().timestamp(),
        novnc_enabled=NOVNC_ENABLED,
    )


@router.get("/ui/media/instagram/auth/session/state", response_class=JSONResponse)
async def instagram_auth_session_state_from_cookie(request: Request):
    auth_task_id = request.cookies.get(INSTAGRAM_AUTH_TASK_COOKIE)
    if not auth_task_id:
        return {
            "status": "missing",
            "authenticated": False,
            "username": None,
            "error": "Instagram auth session was not found.",
            "should_close": False,
        }
    return await instagram_auth_session_state(request, auth_task_id)


@router.post("/ui/media/instagram/auth/session/close", response_class=JSONResponse)
async def instagram_auth_session_close(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")

    client_session_id = request.headers.get(SESSION_HEADER)
    session_dir = media_api._session_dir(client_session_id)
    await _stop_active_instagram_auth_for_session(client_session_id, session_dir)
    response = JSONResponse({"status": "cancelled"})
    response.delete_cookie(INSTAGRAM_AUTH_TASK_COOKIE)
    return response


@router.post("/ui/media/instagram/auth/reset", response_class=HTMLResponse)
async def instagram_auth_reset_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    client_session_id = request.headers.get(SESSION_HEADER)
    session_dir = media_api._session_dir(client_session_id)
    await _stop_active_instagram_auth_for_session(client_session_id, session_dir)
    media_api._reset_instagram_auth(session_dir)
    response = _render_instagram_auth_panel(request, reset_done=True)
    response.delete_cookie(INSTAGRAM_AUTH_TASK_COOKIE)
    return response


@router.get("/ui/music/forms/{workflow}", response_class=HTMLResponse)
async def music_form_partial(request: Request, workflow: str):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    if workflow in {"download", "song", "youtube", "spotify-link"}:
        set_music_active_form(_ui_session_id(request), "download")
    elif workflow == "spotify-library":
        set_music_active_form(_ui_session_id(request), "spotify-library")
    template_map = {
        "download": "partials/music_song_form.html",
        "song": "partials/music_song_form.html",
        "youtube": "partials/music_youtube_form.html",
        "spotify-link": "partials/music_link_form.html",
        "spotify-library": "partials/music_spotify_auth_form.html",
    }
    template_name = template_map.get(workflow)
    if not template_name:
        raise HTTPException(404, "Unknown music workflow.")
    return _render(request, template_name)


@router.get("/ui/music/downloads/tray", response_class=HTMLResponse)
async def music_download_tray(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    return _render(
        request,
        "partials/music_download_tray.html",
        **_music_download_tray_context(request),
    )


@router.get("/ui/telegram/runtime", response_class=HTMLResponse)
async def telegram_runtime_partial(request: Request):
    if not TELEGRAM_NODE_ENABLED:
        raise HTTPException(404, "Telegram node is disabled.")
    task = get_task(telegram_api._agent_task_id) if telegram_api._agent_task_id else None
    status_label = task.status if task else "stopped"
    return _render(
        request,
        "partials/telegram_runtime_card.html",
        task=task,
        status_label=status_label,
    )


@router.post("/ui/telegram/start", response_class=HTMLResponse)
async def telegram_start_submit(request: Request):
    if not TELEGRAM_NODE_ENABLED:
        raise HTTPException(404, "Telegram node is disabled.")
    existing = get_task(telegram_api._agent_task_id) if telegram_api._agent_task_id else None
    if existing and existing.status in {"queued", "running"}:
        return await telegram_runtime_partial(request)

    task = await submit_async_job(
        "telegram.agent",
        lambda: telegram_api.run_agent(),
        submitted_by=_request_client_id(request),
    )
    telegram_api._agent_task_id = task.id
    return await telegram_runtime_partial(request)


@router.post("/ui/telegram/stop", response_class=HTMLResponse)
async def telegram_stop_submit(request: Request):
    if not TELEGRAM_NODE_ENABLED:
        raise HTTPException(404, "Telegram node is disabled.")
    if telegram_api._agent_task_id:
        await cancel_task(telegram_api._agent_task_id)
        telegram_api._agent_task_id = None
    return await telegram_runtime_partial(request)


@router.post("/ui/media/youtube/submit", response_class=HTMLResponse)
async def media_youtube_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    try:
        inputs = _build_media_youtube_inputs(form)
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    task = await submit_bound_job(
        "media.youtube",
        lambda task: media_api._youtube_job(task.id, inputs, None),
        submitted_by=_request_client_id(request),
        meta=_media_meta(
            "YouTube transcription",
            "YouTube input",
            f"{inputs[0]} + {len(inputs) - 1} more" if len(inputs) > 1 else inputs[0],
            submitted_count=len(inputs),
        ),
    )
    remember_media_task(_ui_session_id(request), "youtube", task.id)
    return _render_task_card(request, task.id, title="Media Task", container_id="media-youtube-task-panel")


@router.post("/ui/media/instagram/submit", response_class=HTMLResponse)
async def media_instagram_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    mode = (form.get("instagram_mode") or "posts").strip()
    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))

    if mode == "posts":
        try:
            urls = _build_media_bulk_inputs(form)
        except InputResolutionError as exc:
            return _render_error_panel(request, str(exc))

        task = await submit_bound_job(
            "media.ig_bulk",
            lambda task: media_api._ig_bulk_job(task.id, urls, None, session_dir),
            submitted_by=_request_client_id(request),
            meta=_media_meta(
                "Instagram posts",
                "URLs",
                f"{urls[0]} + {len(urls) - 1} more" if len(urls) > 1 else urls[0],
                submitted_count=len(urls),
            ),
        )
        set_instagram_active_mode(_ui_session_id(request), "posts")
        remember_media_task(_ui_session_id(request), "instagram_posts", task.id)
        return _render_task_card(
            request,
            task.id,
            title="Media Task",
            container_id="media-instagram-posts-task-panel",
        )

    if mode == "public_profile":
        if not _is_instagram_authenticated_for_request(request):
            return _render_instagram_auth_required(request, "public profile scraping")
        username = (form.get("username") or "").strip()
        if not username:
            return _render_error_panel(request, "Instagram username is required.")
        first_n_raw = (form.get("first_n") or "3").strip()
        try:
            first_n = max(int(first_n_raw), 1)
        except ValueError as exc:
            return _render_error_panel(request, str(exc))

        task = await submit_bound_job(
            "media.public_user",
            lambda task: media_api._public_user_job(
                task.id, username, first_n, None, session_dir
            ),
            submitted_by=_request_client_id(request),
            meta=_media_meta(
                "Instagram public profile",
                "Profile",
                f"@{username} - first {first_n} posts",
                submitted_count=first_n,
            ),
        )
        set_instagram_active_mode(_ui_session_id(request), "public_profile")
        remember_media_task(_ui_session_id(request), "instagram_public_profile", task.id)
        return _render_task_card(
            request,
            task.id,
            title="Media Task",
            container_id="media-instagram-public-profile-task-panel",
        )

    if mode == "private_collection":
        if not _is_instagram_authenticated_for_request(request):
            return _render_instagram_auth_required(request, "private collection scraping")
        collection = (form.get("collection") or "").strip()
        if not collection:
            return _render_error_panel(request, "Collection name is required.")

        task = await submit_bound_job(
            "media.private_user",
            lambda task: media_api._private_user_job(
                task.id, collection, None, None, None, session_dir
            ),
            submitted_by=_request_client_id(request),
            meta=_media_meta("Instagram private collection", "Collection", collection),
        )
        set_instagram_active_mode(_ui_session_id(request), "private_collection")
        remember_media_task(_ui_session_id(request), "instagram_private_collection", task.id)
        return _render_task_card(
            request,
            task.id,
            title="Media Task",
            container_id="media-instagram-private-collection-task-panel",
        )

    return _render_error_panel(request, "Unknown Instagram source.")


@router.post("/ui/media/post/submit", response_class=HTMLResponse)
async def media_post_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    url = (form.get("url") or "").strip()
    if not url:
        return _render_error_panel(request, "Instagram post URL is required.")
    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))
    task = await submit_bound_job(
        "media.post",
        lambda task: media_api._post_job(task.id, url, None, session_dir),
        submitted_by=_request_client_id(request),
        meta=_media_meta("Instagram post", "Post URL", url),
    )
    set_instagram_active_mode(_ui_session_id(request), "posts")
    remember_media_task(_ui_session_id(request), "instagram_posts", task.id)
    return _render_task_card(request, task.id, title="Media Task", container_id="media-instagram-posts-task-panel")


@router.post("/ui/media/public-user/submit", response_class=HTMLResponse)
async def media_public_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    username = (form.get("username") or "").strip()
    if not username:
        return _render_error_panel(request, "Instagram username is required.")

    first_n_raw = (form.get("first_n") or "3").strip()
    try:
        first_n = max(int(first_n_raw), 1)
    except ValueError as exc:
        return _render_error_panel(request, str(exc))

    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))
    if not media_api._is_instagram_authenticated(session_dir):
        return _render_instagram_auth_required(request, "public profile scraping")
    task = await submit_bound_job(
        "media.public_user",
        lambda task: media_api._public_user_job(
            task.id, username, first_n, None, session_dir
        ),
        submitted_by=_request_client_id(request),
        meta=_media_meta(
            "Instagram public profile",
            "Profile",
            f"@{username} - first {first_n} posts",
            submitted_count=first_n,
        ),
    )
    set_instagram_active_mode(_ui_session_id(request), "public_profile")
    remember_media_task(_ui_session_id(request), "instagram_public_profile", task.id)
    return _render_task_card(request, task.id, title="Media Task", container_id="media-instagram-public-profile-task-panel")


@router.post("/ui/media/private-user/submit", response_class=HTMLResponse)
async def media_private_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    collection = (form.get("collection") or "").strip()
    if not collection:
        return _render_error_panel(request, "Collection name is required.")

    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))
    if not media_api._is_instagram_authenticated(session_dir):
        return _render_instagram_auth_required(request, "private collection scraping")
    task = await submit_bound_job(
        "media.private_user",
        lambda task: media_api._private_user_job(
            task.id, collection, None, None, None, session_dir
        ),
        submitted_by=_request_client_id(request),
        meta=_media_meta("Private collection scrape", "Collection", collection),
    )
    set_instagram_active_mode(_ui_session_id(request), "private_collection")
    remember_media_task(_ui_session_id(request), "instagram_private_collection", task.id)
    return _render_task_card(request, task.id, title="Media Task", container_id="media-instagram-private-collection-task-panel")


@router.post("/ui/media/bulk/submit", response_class=HTMLResponse)
async def media_bulk_submit(request: Request):
    if not MEDIA_NODE_ENABLED:
        raise HTTPException(404, "Media node is disabled.")
    form = await request.form()
    try:
        urls = _build_media_bulk_inputs(form)
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    session_dir = media_api._session_dir(request.headers.get(SESSION_HEADER))
    task = await submit_bound_job(
        "media.ig_bulk",
        lambda task: media_api._ig_bulk_job(task.id, urls, None, session_dir),
        submitted_by=_request_client_id(request),
        meta=_media_meta(
            "Instagram batch",
            "URLs",
            f"{urls[0]} + {len(urls) - 1} more" if len(urls) > 1 else urls[0],
            submitted_count=len(urls),
        ),
    )
    set_instagram_active_mode(_ui_session_id(request), "posts")
    remember_media_task(_ui_session_id(request), "instagram_posts", task.id)
    return _render_task_card(request, task.id, title="Media Task", container_id="media-instagram-posts-task-panel")


@router.post("/ui/music/song/submit", response_class=HTMLResponse)
async def music_song_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    try:
        queries = _build_music_song_inputs(form)
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    task = await submit_bound_job(
        "music.song",
        lambda task: music_api._song_job(task.id, queries, None),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Music download",
            "Input",
            f"{queries[0]} + {len(queries) - 1} more" if len(queries) > 1 else queries[0],
            submitted_count=len(queries),
        ),
    )
    remember_music_task(_ui_session_id(request), "task", task.id)
    return _render_task_card(request, task.id, title="Music Task", container_id="music-task-panel")


@router.post("/ui/music/youtube/submit", response_class=HTMLResponse)
async def music_yt_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    try:
        inputs = _build_music_yt_inputs(form)
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    task = await submit_bound_job(
        "music.yt",
        lambda task: music_api._yt_job(task.id, inputs, None),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Music link processing",
            "Input",
            f"{inputs[0]} + {len(inputs) - 1} more" if len(inputs) > 1 else inputs[0],
            submitted_count=len(inputs),
        ),
    )
    remember_music_task(_ui_session_id(request), "task", task.id)
    return _render_task_card(request, task.id, title="Music Task", container_id="music-task-panel")


@router.post("/ui/music/link/submit", response_class=HTMLResponse)
async def music_link_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    try:
        urls = _build_music_link_inputs(form)
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    task = await submit_bound_job(
        "music.link",
        lambda task: music_api._link_job(task.id, urls, None),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Music link processing",
            "Music URL",
            f"{urls[0]} + {len(urls) - 1} more" if len(urls) > 1 else urls[0],
            submitted_count=len(urls),
        ),
    )
    remember_music_task(_ui_session_id(request), "task", task.id)
    return _render_task_card(request, task.id, title="Music Task", container_id="music-task-panel")


@router.post("/ui/music/user/auth/start", response_class=HTMLResponse)
async def music_auth_start_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    set_music_active_form(_ui_session_id(request), "spotify-library")
    try:
        session = spotify_auth_sessions.start_session()
    except SpotifyAuthConfigurationError as exc:
        session = spotify_auth_sessions.start_configuration_error_session(str(exc))
    remember_music_auth_session(_ui_session_id(request), session.id)
    return _render(
        request,
        "partials/spotify_auth_card.html",
        session=session,
        poll_url=f"/ui/music/auth/session/{session.id}/card",
        redirect_uri=spotify_auth_sessions.redirect_uri,
    )


@router.get("/ui/music/auth/session/{auth_session_id}/card", response_class=HTMLResponse)
async def spotify_auth_card(request: Request, auth_session_id: str):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    session = spotify_auth_sessions.get_session(auth_session_id)
    if not session:
        return _render(request, "partials/stale_auth_card.html")
    set_music_active_form(_ui_session_id(request), "spotify-library")
    remember_music_auth_session(_ui_session_id(request), session.id)
    return _render(
        request,
        "partials/spotify_auth_card.html",
        session=session,
        poll_url=f"/ui/music/auth/session/{session.id}/card",
        redirect_uri=spotify_auth_sessions.redirect_uri,
    )


@router.post("/ui/music/auth/session/{auth_session_id}/closed", response_class=HTMLResponse)
async def spotify_auth_window_closed(request: Request, auth_session_id: str):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    try:
        session = spotify_auth_sessions.abandon_session(
            auth_session_id,
            "Spotify approval window was closed before authorization completed.",
        )
    except Exception:
        return _render(request, "partials/stale_auth_card.html")

    set_music_active_form(_ui_session_id(request), "spotify-library")
    remember_music_auth_session(_ui_session_id(request), session.id)
    return _render(
        request,
        "partials/spotify_auth_card.html",
        session=session,
        poll_url=f"/ui/music/auth/session/{session.id}/card",
        redirect_uri=spotify_auth_sessions.redirect_uri,
    )


@router.post("/ui/music/user/auth/complete", response_class=HTMLResponse)
async def music_auth_complete_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    auth_session_id = (form.get("auth_session_id") or "").strip()
    redirected_url = (form.get("redirected_url") or "").strip()
    code = (form.get("code") or "").strip() or None
    state = None
    if redirected_url:
        parsed = urlparse(redirected_url)
        values = parse_qs(parsed.query)
        code = code or (values.get("code") or [None])[0]
        state = (values.get("state") or [None])[0]
        if not code and "://" not in redirected_url:
            code = redirected_url
    if not auth_session_id:
        return _render_error_panel(request, "Spotify auth session is missing.")
    if not code:
        return _render_error_panel(request, "Paste the Spotify redirect URL or authorization code.")

    try:
        session = spotify_auth_sessions.complete_from_code(auth_session_id, code, state=state)
    except Exception as exc:
        return _render_error_panel(request, str(exc), title="Spotify Authorization Failed")

    set_music_active_form(_ui_session_id(request), "spotify-library")
    remember_music_auth_session(_ui_session_id(request), session.id)
    return _render(
        request,
        "partials/spotify_auth_card.html",
        session=session,
        poll_url=f"/ui/music/auth/session/{session.id}/card",
        redirect_uri=spotify_auth_sessions.redirect_uri,
    )


@router.post("/ui/music/user/playlists/submit", response_class=HTMLResponse)
async def music_user_playlists_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    set_music_active_form(_ui_session_id(request), "spotify-library")
    if not spotify_auth_sessions.has_cached_user_token():
        return _render_error_panel(
            request,
            "Spotify user authorization is required before fetching playlists.",
            title="Spotify Authorization Required",
            status_code=status.HTTP_409_CONFLICT,
        )

    task = await submit_bound_job(
        "music.user_playlists",
        lambda task: music_api._user_library_job(),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Library selection",
            "Library",
            "Fetch playlists and tracks",
            submitted_count=1,
        ),
    )
    set_music_active_form(_ui_session_id(request), "spotify-library")
    remember_music_task(_ui_session_id(request), "library", task.id)
    return _render_task_card(request, task.id, title="Spotify Library Task", container_id="music-library-panel")


@router.post("/ui/music/user/download/submit", response_class=HTMLResponse)
async def music_user_download_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    playlist_names = form.getlist("playlists")
    track_refs = form.getlist("tracks")
    download_all = (form.get("download_all") or "").strip().lower() == "true"
    try:
        if download_all:
            selected = ["all"]
            track_refs = []
        elif playlist_names:
            selected = resolve_multi_input(direct_values=playlist_names, input_file=None)
        elif track_refs:
            selected = []
        else:
            raise InputResolutionError("Select at least one playlist or track.")
    except (InputResolutionError, ValueError) as exc:
        return _render_error_panel(request, str(exc))

    submitted_count = len(track_refs) if track_refs else len(selected)
    task = await submit_bound_job(
        "music.user_download",
        lambda task: music_api._user_download_job(task.id, selected, None, track_refs),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Library batch download",
            "Selection",
            "Selected tracks" if track_refs else ("All playlists" if selected == ["all"] else f"{len(selected)} playlist(s)"),
            submitted_count=max(submitted_count, 1),
        ),
    )
    set_music_active_form(_ui_session_id(request), "spotify-library")
    remember_music_task(_ui_session_id(request), "library_task", task.id)
    return _render_task_card(request, task.id, title="Music Task", container_id="music-library-task-panel")


@router.post("/ui/music/candidates/download", response_class=HTMLResponse)
async def music_candidate_download_submit(request: Request):
    if not MUSIC_NODE_ENABLED:
        raise HTTPException(404, "Music node is disabled.")
    form = await request.form()
    urls = [value.strip() for value in form.getlist("candidate_urls") if value.strip()]
    container_id = (form.get("container_id") or "music-task-panel").strip()
    if container_id not in {
        "music-task-panel",
        "music-library-task-panel",
        "task-detail-panel",
    }:
        container_id = "music-task-panel"
    if not urls:
        return _render_error_panel(request, "Select at least one candidate to download.")
    if any(not music_api._is_youtube_url(url) for url in urls):
        return _render_error_panel(request, "One or more selected candidates are invalid.")

    task = await submit_bound_job(
        "music.candidate_download",
        lambda task: music_api._candidate_download_job(task.id, urls, None),
        submitted_by=_request_client_id(request),
        meta=_music_task_meta(
            request,
            "Candidate download",
            "Selected candidates",
            f"{len(urls)} selected candidate(s)",
            submitted_count=len(urls),
        ),
    )
    remember_music_task(_ui_session_id(request), container_id, task.id)
    return _render_task_card(
        request,
        task.id,
        title="Music Task",
        container_id=container_id,
    )


@router.get("/ui/tasks/{task_id}/card", response_class=HTMLResponse)
async def task_card_partial(request: Request, task_id: str, container_id: str = "task-panel"):
    if not get_task(task_id):
        return _render(request, "partials/stale_task_card.html")
    return _render_task_card(request, task_id, container_id=container_id)


@router.get("/ui/tasks/{task_id}/artifacts", response_class=HTMLResponse)
async def task_artifact_partial(request: Request, task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")
    return _render(
        request,
        "partials/artifact_list.html",
        task=task,
        artifacts=task.artifacts,
    )
