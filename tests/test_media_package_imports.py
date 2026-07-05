import importlib
from pathlib import Path


def test_media_youtube_fetcher_uses_package_relative_utils_import():
    module = importlib.import_module("sift.engines.media.youtube.youtube")

    assert hasattr(module, "YoutubeFetcher")


def test_media_cli_imports_are_package_relative_after_src_migration():
    source = Path("src/sift/engines/media/cli.py").read_text(encoding="utf-8")

    assert "from .utils import ensure_dir, sanitize_filename" in source
    assert "from .processor import ModelHandler" in source
    assert "from utils import" not in source
    assert "from processor import" not in source
