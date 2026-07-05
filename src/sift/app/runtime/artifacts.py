"""
Helpers for ephemeral job output directories and artifact registration.
"""
from __future__ import annotations

import mimetypes
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

from ..settings import ROOT_DIR


ARTIFACT_ROOT = ROOT_DIR / "var" / "artifacts"
EPHEMERAL_TTL = timedelta(hours=2)

_artifact_index: dict[str, dict[str, dict[str, Any]]] = {}
_artifact_expirations: dict[str, datetime] = {}


def ensure_artifact_root() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    public = artifact.copy()
    public.pop("absolute_path", None)
    return public


def create_job_output_dir(task_id: str, service: str, ephemeral: bool,
                          persistent_outdir: Optional[str] = None) -> tuple[Path, str]:
    if persistent_outdir:
        outdir = Path(persistent_outdir).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        return outdir, "persistent"

    if ephemeral:
        ensure_artifact_root()
        service_slug = service.replace(".", "_")
        outdir = ARTIFACT_ROOT / task_id / service_slug
        outdir.mkdir(parents=True, exist_ok=True)
        _artifact_expirations[task_id] = datetime.now(timezone.utc) + EPHEMERAL_TTL
        return outdir, "ephemeral"

    raise ValueError("Persistent outputs require an explicit outdir.")


def _suffix_filter(include_suffixes: Optional[Iterable[str]]) -> Optional[set[str]]:
    if include_suffixes is None:
        return None
    return {
        suffix.lower() if str(suffix).startswith(".") else f".{str(suffix).lower()}"
        for suffix in include_suffixes
    }


def _iter_artifact_files(base_dir: Path, suffix_filter: Optional[set[str]]) -> list[Path]:
    if not base_dir.exists():
        return []
    files = []
    for path in sorted(p for p in base_dir.rglob("*") if p.is_file()):
        if suffix_filter and path.suffix.lower() not in suffix_filter:
            continue
        files.append(path)
    return files


def _artifact_metadata(task_id: str, base_dir: Path, path: Path) -> dict[str, Any]:
    artifact_id = path.relative_to(base_dir).as_posix()
    encoded_artifact_id = quote(artifact_id, safe="/")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "artifact_id": artifact_id,
        "name": path.name,
        "relative_path": artifact_id,
        "absolute_path": str(path),
        "content_type": content_type,
        "size_bytes": path.stat().st_size,
        "download_url": f"/api/tasks/{task_id}/artifacts/{encoded_artifact_id}",
    }


def _write_zip_bundle(base_dir: Path, files: list[Path], bundle_name: str) -> Path:
    """Create a ZIP under ``base_dir`` while preserving relative paths."""
    safe_name = Path(bundle_name).name or "artifacts.zip"
    if not safe_name.lower().endswith(".zip"):
        safe_name = f"{safe_name}.zip"

    bundle_path = base_dir / safe_name
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    if bundle_path.exists():
        bundle_path.unlink()

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.resolve() == bundle_path.resolve():
                continue
            try:
                arcname = path.relative_to(base_dir).as_posix()
            except ValueError:
                arcname = path.name
            archive.write(path, arcname)
    return bundle_path


def register_directory_artifacts(task_id: str, base_dir: Path,
                                 include_suffixes: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    artifacts = [
        _artifact_metadata(task_id, base_dir, path)
        for path in _iter_artifact_files(base_dir, _suffix_filter(include_suffixes))
    ]

    _artifact_index[task_id] = {artifact["artifact_id"]: artifact for artifact in artifacts}
    return [_public_artifact(artifact) for artifact in artifacts]


def register_directory_artifacts_with_optional_bundle(
    task_id: str,
    base_dir: Path,
    *,
    include_suffixes: Optional[Iterable[str]] = None,
    bundle_if_count_gt: int = 10,
    bundle_name: Optional[str] = None,
    replace_with_bundle: bool = False,
) -> list[dict[str, Any]]:
    """Register directory artifacts and optionally create a ZIP for large outputs.

    ``include_suffixes`` defines the user-facing files that count toward the
    threshold and get included in the bundle. When ``replace_with_bundle`` is
    true, only the ZIP is exposed as the task artifact. Otherwise, the ZIP is
    exposed first and the individual artifacts remain available too.
    """
    suffixes = _suffix_filter(include_suffixes)
    files = _iter_artifact_files(base_dir, suffixes)

    should_bundle = len(files) > bundle_if_count_gt
    if should_bundle:
        bundle_path = _write_zip_bundle(
            base_dir,
            files,
            bundle_name or f"{task_id}-artifacts.zip",
        )
        visible_files = [bundle_path] if replace_with_bundle else [bundle_path, *files]
    else:
        visible_files = files

    artifacts = [_artifact_metadata(task_id, base_dir, path) for path in visible_files]
    _artifact_index[task_id] = {artifact["artifact_id"]: artifact for artifact in artifacts}
    return [_public_artifact(artifact) for artifact in artifacts]


def list_artifacts(task_id: str) -> list[dict[str, Any]]:
    artifacts = []
    for artifact in _artifact_index.get(task_id, {}).values():
        artifact = _public_artifact(artifact)
        artifact["download_url"] = (
            f"/api/tasks/{task_id}/artifacts/"
            f"{quote(artifact['artifact_id'], safe='/')}"
        )
        artifacts.append(artifact)
    return artifacts


def resolve_artifact_path(task_id: str, artifact_id: str) -> Optional[Path]:
    metadata = _artifact_index.get(task_id, {}).get(artifact_id)
    if metadata:
        path = Path(metadata["absolute_path"])
        return path if path.exists() else None

    task_root = (ARTIFACT_ROOT / task_id).resolve()
    if not task_root.exists():
        return None

    for service_dir in task_root.iterdir():
        if not service_dir.is_dir():
            continue
        candidate = (service_dir / artifact_id).resolve()
        if candidate.is_file() and task_root in candidate.parents:
            return candidate
    return None


def get_artifact_expiry(task_id: str) -> Optional[str]:
    expiry = _artifact_expirations.get(task_id)
    return expiry.isoformat() if expiry else None


def cleanup_expired_artifacts(now: Optional[datetime] = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [task_id for task_id, expiry in _artifact_expirations.items() if expiry <= current]
    for task_id in expired:
        shutil.rmtree(ARTIFACT_ROOT / task_id, ignore_errors=True)
        _artifact_expirations.pop(task_id, None)
        _artifact_index.pop(task_id, None)

    if not ARTIFACT_ROOT.exists():
        return

    cutoff = current - EPHEMERAL_TTL
    for task_dir in ARTIFACT_ROOT.iterdir():
        if not task_dir.is_dir() or task_dir.name in _artifact_expirations:
            continue
        try:
            modified_at = datetime.fromtimestamp(task_dir.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if modified_at <= cutoff:
            shutil.rmtree(task_dir, ignore_errors=True)
            _artifact_index.pop(task_dir.name, None)
