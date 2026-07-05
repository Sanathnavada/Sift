import zipfile
from pathlib import Path

from sift.app.api.routes.media import _register_user_media_artifacts
from sift.app.api.routes.music import _register_music_artifacts
from sift.app.runtime.artifacts import resolve_artifact_path
from sift.app.runtime.music_download_tray import _is_music_artifact


def _write_files(base: Path, count: int, suffix: str = ".jpg") -> None:
    base.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (base / f"file-{index:02d}{suffix}").write_bytes(f"payload-{index}".encode())


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_media_outputs_are_not_bundled_at_threshold(tmp_path):
    job_dir = tmp_path / "media-task"
    _write_files(job_dir / "post_abc", 10, ".jpg")

    artifacts = _register_user_media_artifacts("media-threshold", job_dir)

    assert len(artifacts) == 10
    assert not any(artifact["name"].endswith(".zip") for artifact in artifacts)


def test_media_outputs_over_threshold_are_replaced_by_zip(tmp_path):
    job_dir = tmp_path / "media-task"
    _write_files(job_dir / "post_abc", 11, ".jpg")

    artifacts = _register_user_media_artifacts("media-bundle", job_dir)

    assert [artifact["name"] for artifact in artifacts] == ["media-output-media-bundle.zip"]
    zip_path = resolve_artifact_path("media-bundle", artifacts[0]["artifact_id"])
    assert zip_path and zip_path.exists()
    assert len(_zip_names(zip_path)) == 11
    assert all(name.endswith(".jpg") for name in _zip_names(zip_path))


def test_music_outputs_are_not_bundled_at_threshold(tmp_path):
    job_dir = tmp_path / "music-task"
    _write_files(job_dir / "Playlist", 10, ".m4a")
    (job_dir / "Playlist" / "Playlist.m3u").write_text("#EXTM3U\n", encoding="utf-8")

    artifacts = _register_music_artifacts("music-threshold", job_dir)

    assert len(artifacts) == 10
    assert not any(artifact["name"].endswith(".zip") for artifact in artifacts)
    assert all(artifact["name"].endswith(".m4a") for artifact in artifacts)


def test_music_outputs_over_threshold_add_zip_and_keep_audio_files(tmp_path):
    job_dir = tmp_path / "music-task"
    _write_files(job_dir / "Playlist", 11, ".m4a")
    (job_dir / "Playlist" / "Playlist.m3u").write_text("#EXTM3U\n", encoding="utf-8")

    artifacts = _register_music_artifacts("music-bundle", job_dir)

    names = [artifact["name"] for artifact in artifacts]
    assert names[0] == "music-downloads-music-bundle.zip"
    assert len(artifacts) == 12
    assert sum(name.endswith(".m4a") for name in names) == 11
    assert not any(name.endswith(".m3u") for name in names)
    assert not _is_music_artifact(artifacts[0])

    zip_path = resolve_artifact_path("music-bundle", artifacts[0]["artifact_id"])
    assert zip_path and zip_path.exists()
    assert _zip_names(zip_path) == {f"Playlist/file-{index:02d}.m4a" for index in range(11)}
