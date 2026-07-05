"""
Form parsing helpers for server-rendered UI routes.

The route handlers stay responsible for submission side effects. This module only
normalizes browser form data into validated lists of input strings.
"""
from __future__ import annotations

import re
from typing import Optional

from ..runtime.input_resolver import InputResolutionError, resolve_multi_input


INSTAGRAM_POST_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+/?(?:\?[^ \t\r\n<>'\"]*)?",
    re.IGNORECASE,
)


def _normalize_lines(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _resolve_ui_values(values: list[str], empty_message: str) -> list[str]:
    if not values:
        raise InputResolutionError(empty_message)
    return resolve_multi_input(direct_values=values, input_file=None)


def _build_music_song_inputs(form_data) -> list[str]:
    direct_values = []
    query = (form_data.get("query") or "").strip()
    if query:
        direct_values.append(query)
    direct_values.extend(_normalize_lines(form_data.get("queries_text")))
    return _resolve_ui_values(direct_values, "Provide at least one song, YouTube link, or Spotify link.")


def _build_music_yt_inputs(form_data) -> list[str]:
    direct_values = []
    single = (form_data.get("input") or "").strip()
    if single:
        direct_values.append(single)
    direct_values.extend(_normalize_lines(form_data.get("inputs_text")))
    return _resolve_ui_values(direct_values, "Provide at least one YouTube input.")


def _build_music_link_inputs(form_data) -> list[str]:
    direct_values = []
    single = (form_data.get("url") or "").strip()
    if single:
        direct_values.append(single)
    direct_values.extend(_normalize_lines(form_data.get("urls_text")))
    return _resolve_ui_values(direct_values, "Provide at least one Spotify URL.")


def _build_media_youtube_inputs(form_data) -> list[str]:
    direct_values = []
    single = (form_data.get("input") or "").strip()
    if single:
        direct_values.append(single)
    direct_values.extend(_normalize_lines(form_data.get("inputs_text")))
    return _resolve_ui_values(direct_values, "Provide at least one YouTube input.")


def _build_media_bulk_inputs(form_data) -> list[str]:
    text = form_data.get("urls_text") or ""
    direct_values = INSTAGRAM_POST_URL_RE.findall(text)
    if not direct_values:
        direct_values = _normalize_lines(text)
    return _resolve_ui_values(direct_values, "Provide at least one Instagram URL.")
