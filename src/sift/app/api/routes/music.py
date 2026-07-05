"""
Music pipeline API routes.
"""
import html
import json
from urllib.parse import parse_qs, urlparse
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ...runtime.artifacts import (
    create_job_output_dir,
    get_artifact_expiry,
    register_directory_artifacts_with_optional_bundle,
)
from ...runtime.auth_sessions import SpotifyAuthConfigurationError, spotify_auth_sessions
from ...runtime.music_download_tray import AUDIO_SUFFIXES
from ...runtime.input_resolver import InputResolutionError, resolve_multi_input
from ...runtime.tasks import append_task_event, submit_bound_job

from sift.engines.music.models import Track
from sift.engines.music.services.youtube import YouTubeResolver

router = APIRouter(prefix="/music", tags=["Music"])


def _spotify_provider(*, use_user_auth: bool, allow_interactive: bool = True):
    from sift.engines.music.services.spotify import SpotifyProvider

    return SpotifyProvider(
        use_user_auth=use_user_auth,
        allow_interactive=allow_interactive,
    )


def _music_downloader(*, ephemeral: bool = False, outdir=None):
    from sift.engines.music.services.downloader import MusicDownloader

    return MusicDownloader(ephemeral=ephemeral, outdir=outdir)


def _is_youtube_url(text: str) -> bool:
    return text.startswith("http") and ("youtube.com" in text or "youtu.be" in text)


def _is_spotify_url(text: str) -> bool:
    return text.startswith("http") and "open.spotify.com" in text


def _accepted_response(task) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "task_id": task.id,
            "status": task.status,
            "queue_position": task.queue_position,
            "lane": task.meta.get("lane"),
            "lane_label": task.meta.get("lane_label"),
            "queue_message": task.meta.get("queue_message"),
            "estimated_wait_seconds": task.meta.get("estimated_wait_seconds"),
            "estimated_runtime_seconds": task.meta.get("estimated_runtime_seconds"),
            "estimated_total_seconds": task.meta.get("estimated_total_seconds"),
            "poll_url": f"/api/tasks/{task.id}",
        },
    )


def _build_result(*, task_id: str, message: str, input_payload: dict,
                  output_payload: dict, stats: dict,
                  artifacts: list[dict], items: Optional[list[dict]] = None) -> dict:
    result = {
        "message": message,
        "input": input_payload,
        "output": output_payload,
        "artifacts": artifacts,
        "stats": stats,
    }
    if items is not None:
        result["items"] = items
    expires_at = get_artifact_expiry(task_id)
    if expires_at:
        result["expires_at"] = expires_at
    return result




def _register_music_artifacts(task_id: str, job_outdir) -> list[dict]:
    return register_directory_artifacts_with_optional_bundle(
        task_id,
        job_outdir,
        include_suffixes=AUDIO_SUFFIXES,
        bundle_if_count_gt=10,
        bundle_name=f"music-downloads-{task_id}.zip",
        replace_with_bundle=False,
    )

def _raise_if_all_failed(completed_count: int, failed_count: int, items: list[dict]) -> None:
    if failed_count and completed_count == 0:
        first_error = next((item.get("error") for item in items if item.get("error")), None)
        raise RuntimeError(first_error or "All submitted items failed.")


class SongRequest(BaseModel):
    query: Optional[str] = None
    queries: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class YtRequest(BaseModel):
    input: Optional[str] = None
    inputs: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class LinkRequest(BaseModel):
    url: Optional[str] = None
    urls: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class UserDownloadRequest(BaseModel):
    playlists: Optional[list[str]] = None
    tracks: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class CandidateDownloadRequest(BaseModel):
    urls: list[str]
    outdir: Optional[str] = None


class SpotifyAuthCompleteRequest(BaseModel):
    auth_session_id: str
    code: Optional[str] = None
    state: Optional[str] = None
    redirected_url: Optional[str] = None


def _normalize_song_inputs(req: SongRequest) -> list[str]:
    direct_values = []
    if req.query:
        direct_values.append(req.query)
    if req.queries:
        direct_values.extend(req.queries)
    return resolve_multi_input(direct_values=direct_values or None, input_file=req.input_file)


def _normalize_yt_inputs(req: YtRequest) -> list[str]:
    direct_values = []
    if req.input:
        direct_values.append(req.input)
    if req.inputs:
        direct_values.extend(req.inputs)
    return resolve_multi_input(direct_values=direct_values or None, input_file=req.input_file)


def _normalize_link_inputs(req: LinkRequest) -> list[str]:
    direct_values = []
    if req.url:
        direct_values.append(req.url)
    if req.urls:
        direct_values.extend(req.urls)
    return resolve_multi_input(direct_values=direct_values or None, input_file=req.input_file)


def _normalize_playlist_inputs(req: UserDownloadRequest) -> list[str]:
    return resolve_multi_input(direct_values=req.playlists, input_file=req.input_file)


def _song_job(task_id: str, queries: list[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "music.song", outdir is None, outdir)
    resolver = YouTubeResolver()
    spotify = _spotify_provider(use_user_auth=False)

    items = []
    completed_count = 0
    failed_count = 0
    needs_review_count = 0

    for query in queries:
        try:
            if _is_youtube_url(query):
                append_task_event(task_id, f"Downloading YouTube link: {query}")
                downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
                download_result = downloader.download_all({"Single Downloads": [query]})
                items.append({
                    "input": query,
                    "input_type": "youtube_url",
                    "status": "completed",
                    "resolved_url": query,
                    "files": download_result["files"],
                    "download_stats": download_result["stats"],
                })
            elif _is_spotify_url(query):
                append_task_event(task_id, f"Fetching Spotify metadata: {query}")
                playlists = spotify.fetch_by_url(query)
                resolution_data = resolver.resolve_all(playlists)
                urls = [url for data in resolution_data.values() for url in data.get("urls", [])]
                if not urls:
                    review_items = _candidate_items(resolution_data)
                    if review_items:
                        items.extend(review_items)
                        needs_review_count += len(review_items)
                        continue
                    raise RuntimeError("No YouTube matches were found for this Spotify link.")

                downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
                download_result = downloader.download_all({"Single Downloads": urls})
                items.append({
                    "input": query,
                    "input_type": "spotify_url",
                    "status": "completed",
                    "resolved_url_count": len(urls),
                    "files": download_result["files"],
                    "download_stats": download_result["stats"],
                })
            else:
                track = Track(title=query, is_dummy=True)
                resolution = resolver.resolve_track(track)
                url = resolution["url"]
                if not url:
                    items.append({
                        "input": query,
                        "input_type": "search_query",
                        "status": "needs review",
                        "error": resolution["error"],
                        "candidates": resolution["candidates"],
                    })
                    needs_review_count += 1
                    continue

                downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
                download_result = downloader.download_all({"Single Downloads": [url]})
                items.append({
                    "input": query,
                    "input_type": "search_query",
                    "status": "completed",
                    "resolved_url": url,
                    "files": download_result["files"],
                    "download_stats": download_result["stats"],
                })
            completed_count += 1
        except Exception as exc:
            append_task_event(task_id, f"Failed song input: {exc}")
            items.append({"input": query, "status": "failed", "error": str(exc)})
            failed_count += 1

    artifacts = _register_music_artifacts(task_id, job_outdir)
    _raise_if_all_failed(completed_count + needs_review_count, failed_count, items)
    return _build_result(
        task_id=task_id,
        message="Completed song download request",
        input_payload={"queries": queries},
        output_payload={"output_mode": output_mode, "outdir": str(job_outdir)},
        stats={
            "submitted_count": len(queries),
            "completed_count": completed_count,
            "failed_count": failed_count,
            "needs_review_count": needs_review_count,
        },
        artifacts=artifacts,
        items=items,
    )


def _yt_job(task_id: str, inputs: list[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "music.yt", outdir is None, outdir)
    resolver = YouTubeResolver()

    items = []
    completed_count = 0
    failed_count = 0
    needs_review_count = 0

    for input_str in inputs:
        input_type = "youtube_url" if _is_youtube_url(input_str) else "search_query"
        if _is_youtube_url(input_str):
            url = input_str
        else:
            track = Track(title=input_str, is_dummy=True)
            resolution = resolver.resolve_track(track)
            url = resolution["url"]
            if not url:
                items.append({
                    "input": input_str,
                    "input_type": "search_query",
                    "status": "needs_review",
                    "error": resolution["error"],
                    "candidates": resolution["candidates"],
                })
                needs_review_count += 1
                continue

        downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
        download_result = downloader.download_all({"YouTube Downloads": [url]})
        items.append({
            "input": input_str,
            "input_type": input_type,
            "status": "completed",
            "resolved_url": url,
            "files": download_result["files"],
            "download_stats": download_result["stats"],
        })
        completed_count += 1

    artifacts = _register_music_artifacts(task_id, job_outdir)
    _raise_if_all_failed(completed_count + needs_review_count, failed_count, items)
    return _build_result(
        task_id=task_id,
        message="Completed YouTube audio request",
        input_payload={"inputs": inputs},
        output_payload={"output_mode": output_mode, "outdir": str(job_outdir)},
        stats={
            "submitted_count": len(inputs),
            "completed_count": completed_count,
            "failed_count": failed_count,
            "needs_review_count": needs_review_count,
        },
        artifacts=artifacts,
        items=items,
    )


def _link_job(task_id: str, urls: list[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "music.link", outdir is None, outdir)
    resolver = YouTubeResolver()
    spotify = _spotify_provider(use_user_auth=False)

    items = []
    completed_count = 0
    failed_count = 0

    for spotify_url in urls:
        try:
            append_task_event(task_id, f"Fetching Spotify link: {spotify_url}")
            playlists = spotify.fetch_by_url(spotify_url)
            track_count = sum(len(tracks) for tracks in playlists.values())
            append_task_event(task_id, f"Resolved Spotify metadata: {track_count} track(s)")
            resolution_data = resolver.resolve_all(playlists)
            review_items = _candidate_items(resolution_data)
            resolved_url_count = 0
            download_batch_count = 0
            downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
            for name, data in resolution_data.items():
                playlist_urls = data.get("urls", [])
                failed_tracks = data.get("failed", [])
                if failed_tracks:
                    append_task_event(task_id, f"{len(failed_tracks)} track(s) could not be matched on YouTube")
                if playlist_urls:
                    append_task_event(task_id, f"Downloading {len(playlist_urls)} YouTube match(es)")
                    downloader.download_all({name: playlist_urls})
                    resolved_url_count += len(playlist_urls)
                    download_batch_count += 1

            if resolved_url_count == 0 and review_items:
                items.extend(review_items)
                failed_count += 1
                continue
            if resolved_url_count == 0:
                raise RuntimeError("No YouTube matches were found for this Spotify link.")

            items.append({
                "input": spotify_url,
                "status": "completed",
                "playlist_names": list(playlists.keys()),
                "resolved_url_count": resolved_url_count,
                "download_batch_count": download_batch_count,
            })
            completed_count += 1
        except Exception as exc:
            append_task_event(task_id, f"Failed Spotify link: {exc}")
            items.append({"input": spotify_url, "status": "failed", "error": str(exc)})
            failed_count += 1

    artifacts = _register_music_artifacts(task_id, job_outdir)
    _raise_if_all_failed(completed_count, failed_count, items)

    return _build_result(
        task_id=task_id,
        message="Completed Spotify link request",
        input_payload={"urls": urls},
        output_payload={"output_mode": output_mode, "outdir": str(job_outdir)},
        stats={
            "submitted_count": len(urls),
            "completed_count": completed_count,
            "failed_count": failed_count,
        },
        artifacts=artifacts,
        items=items,
    )


def _user_library_job() -> dict:
    spotify = _spotify_provider(use_user_auth=True, allow_interactive=False)
    library = spotify.fetch_user_library()
    skipped_playlists = getattr(spotify, "last_library_errors", {})
    return {
        "message": "Fetched Spotify user library",
        "input": {"mode": "user_playlists"},
        "output": {
            "playlists": [
                {
                    "name": name,
                    "track_count": len(tracks),
                    "image_url": next((track.image_url for track in tracks if track.image_url), ""),
                    "tracks": [
                        {
                            "index": index,
                            "title": track.title,
                            "artist": track.artist,
                            "album": track.album,
                            "image_url": track.image_url,
                            "duration_ms": track.duration_ms,
                        }
                        for index, track in enumerate(tracks)
                    ],
                }
                for name, tracks in library.items()
            ]
        },
        "artifacts": [],
        "stats": {
            "playlist_count": len(library),
            "skipped_playlist_count": len(skipped_playlists),
            "track_count": sum(len(tracks) for tracks in library.values()),
        },
        "warnings": [
            f"Skipped {name}: {error}"
            for name, error in skipped_playlists.items()
        ],
    }


def _track_from_payload(value: str) -> Track:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid selected track payload.") from exc
    title = (payload.get("title") or "").strip()
    if not title:
        raise ValueError("Selected track is missing a title.")
    return Track(
        title=title,
        artist=(payload.get("artist") or "").strip(),
        album=(payload.get("album") or "").strip(),
        image_url=(payload.get("image_url") or "").strip(),
        duration_ms=int(payload.get("duration_ms") or 0),
    )


def _track_key(track: Track) -> tuple[str, str, str]:
    return (
        track.title.strip().casefold(),
        track.artist.strip().casefold(),
        track.album.strip().casefold(),
    )


def _dedupe_playlists(playlists: dict[str, list[Track]]) -> tuple[dict[str, list[Track]], int]:
    seen = set()
    duplicate_count = 0
    deduped = {}
    for name, tracks in playlists.items():
        unique_tracks = []
        for track in tracks:
            key = _track_key(track)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            unique_tracks.append(track)
        if unique_tracks:
            deduped[name] = unique_tracks
    return deduped, duplicate_count


def _candidate_items(resolution_data: dict[str, dict]) -> list[dict]:
    items = []
    for playlist_name, data in resolution_data.items():
        for failure in data.get("failed", []):
            track = failure["track"]
            items.append({
                "input": f"{track.title} - {track.artist}",
                "status": "needs review",
                "error": failure["error"],
                "playlist_name": playlist_name,
                "candidates": failure.get("candidates", []),
            })
    return items


def _candidate_download_job(
    task_id: str,
    urls: list[str],
    outdir: Optional[str],
) -> dict:
    job_outdir, output_mode = create_job_output_dir(
        task_id,
        "music.candidate_download",
        outdir is None,
        outdir,
    )
    downloader = _music_downloader(
        ephemeral=(output_mode == "ephemeral"),
        outdir=job_outdir,
    )
    download_result = downloader.download_all({"Selected Candidates": urls})
    artifacts = _register_music_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed selected candidate download",
        input_payload={"urls": urls},
        output_payload={"output_mode": output_mode, "outdir": str(job_outdir)},
        stats={"submitted_count": len(urls), **download_result["stats"]},
        artifacts=artifacts,
    )


def _user_download_job(
    task_id: str,
    selected: list[str],
    outdir: Optional[str],
    selected_tracks: Optional[list[str]] = None,
) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "music.user_download", outdir is None, outdir)
    selected_tracks = selected_tracks or []

    if selected == ["all"]:
        library = _spotify_provider(use_user_auth=True, allow_interactive=False).fetch_user_library()
        playlists = library
    else:
        library = None
        playlists = {}
        if selected:
            library = _spotify_provider(use_user_auth=True, allow_interactive=False).fetch_user_library()
            playlists = {name: tracks for name, tracks in library.items() if name in selected}
            missing = sorted(set(selected) - set(playlists))
            if missing:
                raise RuntimeError(f"Playlists not found in your library: {missing}")

        chosen_tracks = []
        skipped_tracks = []
        for payload in selected_tracks:
            try:
                chosen_tracks.append(_track_from_payload(payload))
            except (ValueError, TypeError):
                skipped_tracks.append(payload)

        if chosen_tracks:
            playlists["Selected Tracks"] = chosen_tracks
        if skipped_tracks:
            raise RuntimeError("One or more selected tracks could not be read. Refresh the library and try again.")
        if not playlists:
            raise RuntimeError("Select at least one playlist or track.")

    playlists, duplicate_count = _dedupe_playlists(playlists)
    if not playlists:
        raise RuntimeError("No unique tracks remained after removing duplicates.")

    resolver = YouTubeResolver()
    resolution_data = resolver.resolve_all(playlists)
    candidate_items = _candidate_items(resolution_data)

    download_batch_count = 0
    resolved_url_count = 0
    for name, data in resolution_data.items():
        urls = data.get("urls", [])
        if urls:
            downloader = _music_downloader(ephemeral=(output_mode == "ephemeral"), outdir=job_outdir)
            downloader.download_all({name: urls})
            resolved_url_count += len(urls)
            download_batch_count += 1

    artifacts = _register_music_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed Spotify user playlist download",
        input_payload={"playlists": selected, "tracks": selected_tracks},
        output_payload={
            "output_mode": output_mode,
            "outdir": str(job_outdir),
            "playlist_names": list(playlists.keys()),
        },
        stats={
            "playlist_count": len(playlists),
            "resolved_url_count": resolved_url_count,
            "download_batch_count": download_batch_count,
            "duplicate_track_count": duplicate_count,
            "needs_review_count": len(candidate_items),
        },
        artifacts=artifacts,
        items=candidate_items,
    )


@router.post("/song")
async def download_song(req: SongRequest, request: Request):
    try:
        queries = _normalize_song_inputs(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    task = await submit_bound_job(
        "music.song",
        lambda task: _song_job(task.id, queries, req.outdir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Music download",
            "item_label": "Input",
            "item_detail": f"{queries[0]} + {len(queries) - 1} more" if len(queries) > 1 else queries[0],
            "submitted_count": len(queries),
        },
    )
    return _accepted_response(task)


@router.post("/yt")
async def download_yt(req: YtRequest, request: Request):
    try:
        inputs = _normalize_yt_inputs(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    task = await submit_bound_job(
        "music.yt",
        lambda task: _yt_job(task.id, inputs, req.outdir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "YouTube audio download",
            "item_label": "Input",
            "item_detail": f"{inputs[0]} + {len(inputs) - 1} more" if len(inputs) > 1 else inputs[0],
            "submitted_count": len(inputs),
        },
    )
    return _accepted_response(task)


@router.post("/link")
async def download_link(req: LinkRequest, request: Request):
    try:
        urls = _normalize_link_inputs(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    task = await submit_bound_job(
        "music.link",
        lambda task: _link_job(task.id, urls, req.outdir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Spotify link download",
            "item_label": "Spotify URL",
            "item_detail": f"{urls[0]} + {len(urls) - 1} more" if len(urls) > 1 else urls[0],
            "submitted_count": len(urls),
        },
    )
    return _accepted_response(task)


@router.post("/user/auth/start")
async def start_spotify_auth():
    try:
        session = spotify_auth_sessions.start_session()
    except SpotifyAuthConfigurationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "auth_session_id": session.id,
            "status": session.status,
            "authorization_url": session.authorization_url,
            "redirect_uri": spotify_auth_sessions.redirect_uri,
            "poll_url": f"/api/music/user/auth/session/{session.id}",
        },
    )


@router.get("/user/auth/session/{auth_session_id}")
async def get_spotify_auth_status(auth_session_id: str):
    session = spotify_auth_sessions.get_session(auth_session_id)
    if not session:
        raise HTTPException(404, "Auth session not found.")
    payload = {
        "auth_session_id": session.id,
        "status": session.status,
        "authorization_url": session.authorization_url,
        "redirect_uri": spotify_auth_sessions.redirect_uri,
        "created_at": session.created_at,
        "finished_at": session.finished_at,
        "error": session.error,
        "user_id": session.user_id,
        "user_display_name": session.user_display_name,
        "user_email": session.user_email,
    }
    if session.status == "waiting_for_user":
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=payload)
    return payload


def _spotify_callback_html(session, message: str) -> HTMLResponse:
    """Render the OAuth popup completion state using the same visual system as the app.

    The popup usually closes itself after notifying the opener. When the browser
    blocks self-close or the opener is unavailable, this page becomes the user's
    fallback. Keep it styled and useful instead of returning plain browser HTML.
    """
    status = html.escape(str(session.status).replace("_", " "))
    message_text = html.escape(message)
    error_html = ""
    if session.error:
        error_html = (
            '<div class="callout callout--danger">'
            '<strong>Authorization issue</strong>'
            f'<p>{html.escape(session.error)}</p>'
            '</div>'
        )

    user_html = ""
    if getattr(session, "user_display_name", None) or getattr(session, "user_id", None):
        label = html.escape(session.user_display_name or session.user_id)
        suffix = ""
        if getattr(session, "user_id", None) and session.user_id != session.user_display_name:
            suffix = f" <span class=\"muted\">({html.escape(session.user_id)})</span>"
        user_html = (
            '<p class="task-card__body">'
            f'Connected Spotify user: <strong>{label}</strong>{suffix}'
            '</p>'
        )

    status_class = "success" if session.status == "authorized" else "danger" if session.status == "failed" else "accent"
    status_card_url = f"/ui/music/auth/session/{session.id}/card"
    escaped_status_card_url = html.escape(status_card_url, quote=True)
    return HTMLResponse(
        content=(
            "<!DOCTYPE html>"
            "<html lang='en'>"
            "<head>"
            "<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            f"<title>{message_text}</title>"
            "<link rel='stylesheet' href='/static/css/app.css?v=spotify-callback-styled-1'>"
            "</head>"
            "<body>"
            "<main class='auth-callback-shell'>"
            "<article class='task-card auth-callback-card'>"
            "<div class='task-card__header'>"
            "<div>"
            "<span class='task-card__kicker'>Library Connection</span>"
            f"<h2>{message_text}</h2>"
            "</div>"
            f"<span class='status-pill status-pill--{status_class}'>{status}</span>"
            "</div>"
            f"{error_html}"
            f"{user_html}"
            "<p class='task-card__body'>Returning to Sift. If this window does not close automatically, close it and return to the Music page.</p>"
            "<div class='button-row'>"
            f"<a class='button' id='spotify-status-link' href='{escaped_status_card_url}'>Open authorization status</a>"
            "</div>"
            "<p id='spotify-close-message' class='footer__muted' style='display:none'>You can close this window and return to Sift.</p>"
            "</article>"
            "</main>"
            "<script>"
            "(function(){"
            f"var statusUrl = {status_card_url!r};"
            "try { window.localStorage.setItem('gateway.spotifyAuthStatusUrl', statusUrl); } catch (err) {}"
            "if (window.opener && !window.opener.closed) {"
            "  try {"
            "    if (window.opener.htmx) {"
            "      window.opener.htmx.ajax('GET', statusUrl, {target:'#music-library-auth-panel', swap:'innerHTML'});"
            "    } else {"
            "      window.opener.localStorage.setItem('gateway.spotifyAuthStatusUrl', statusUrl);"
            "    }"
            "    window.close();"
            "    return;"
            "  } catch (err) {}"
            "}"
            "setTimeout(function(){"
            "  var msg = document.getElementById('spotify-close-message');"
            "  if (msg) { msg.style.display = 'block'; }"
            "}, 900);"
            "})();"
            "</script>"
            "</body></html>"
        )
    )

async def handle_spotify_auth_callback(
    state: str,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    if error:
        error_message = f"Spotify returned {error}"
        if error_description:
            error_message = f"{error_message}: {error_description}"
        try:
            session = spotify_auth_sessions.fail_from_callback_state(state, error_message)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        return _spotify_callback_html(session, "Spotify authorization failed.")

    if not code:
        try:
            session = spotify_auth_sessions.fail_from_callback_state(state, "Spotify did not return an authorization code.")
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        return _spotify_callback_html(session, "Spotify authorization failed.")

    try:
        session = spotify_auth_sessions.complete_from_callback_state(state, code)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _spotify_callback_html(session, "Spotify authorization complete.")


@router.get("/user/auth/callback", response_class=HTMLResponse)
async def spotify_auth_callback(
    state: str,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    return await handle_spotify_auth_callback(
        state=state,
        code=code,
        error=error,
        error_description=error_description,
    )


@router.post("/user/auth/complete")
async def spotify_auth_complete(req: SpotifyAuthCompleteRequest):
    code = req.code
    state = req.state
    if req.redirected_url:
        parsed = urlparse(req.redirected_url)
        values = parse_qs(parsed.query)
        code = code or (values.get("code") or [None])[0]
        state = state or (values.get("state") or [None])[0]
    if not code:
        raise HTTPException(400, "A Spotify authorization code is required.")

    try:
        session = spotify_auth_sessions.complete_from_code(req.auth_session_id, code, state=state)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "auth_session_id": session.id,
        "status": session.status,
        "finished_at": session.finished_at,
    }


@router.get("/user/playlists")
@router.post("/user/playlists")
async def get_user_playlists(request: Request):
    if not spotify_auth_sessions.has_cached_user_token():
        raise HTTPException(
            409,
            "Spotify user authorization is required. Start it via /api/music/user/auth/start.",
        )

    task = await submit_bound_job(
        "music.user_playlists",
        lambda task: _user_library_job(),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Spotify library",
            "item_label": "Library",
            "item_detail": "Fetch playlists and tracks",
            "submitted_count": 1,
        },
    )
    return _accepted_response(task)


@router.post("/user/download")
async def download_user_playlists(req: UserDownloadRequest, request: Request):
    if not spotify_auth_sessions.has_cached_user_token():
        raise HTTPException(
            409,
            "Spotify user authorization is required. Start it via /api/music/user/auth/start.",
        )

    try:
        selected = _normalize_playlist_inputs(req) if req.playlists else []
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not selected and not req.tracks:
        raise HTTPException(400, "Select at least one playlist or track.")

    task = await submit_bound_job(
        "music.user_download",
        lambda task: _user_download_job(task.id, selected, req.outdir, req.tracks),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Spotify library download",
            "item_label": "Selection",
            "item_detail": "Selected tracks" if req.tracks else f"{len(selected)} playlist(s)",
            "submitted_count": max(len(req.tracks or []), len(selected), 1),
        },
    )
    return _accepted_response(task)


@router.post("/candidate-download")
async def download_candidates(req: CandidateDownloadRequest, request: Request):
    urls = [url.strip() for url in req.urls if _is_youtube_url(url.strip())]
    if not urls or len(urls) != len(req.urls):
        raise HTTPException(400, "Select at least one valid YouTube candidate.")
    task = await submit_bound_job(
        "music.candidate_download",
        lambda task: _candidate_download_job(task.id, urls, req.outdir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Candidate download",
            "item_label": "Selected candidates",
            "item_detail": f"{len(urls)} YouTube candidate(s)",
            "submitted_count": len(urls),
        },
    )
    return _accepted_response(task)
