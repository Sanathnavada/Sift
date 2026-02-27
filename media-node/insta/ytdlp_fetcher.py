import re
import sys
import time
import random
import logging

logger = logging.getLogger(__name__)

try:
    import instaloader
except ImportError:
    logger.error("instaloader not installed. Run: pip install instaloader")
    sys.exit(1)

# Kept for any future yt-dlp usage (YouTube etc.)
try:
    import yt_dlp
except ImportError:
    yt_dlp = None


_SC_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")


def _make_loader() -> "instaloader.Instaloader":
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
    )


class YtdlpInstaFetcher:
    """
    Fetches public Instagram content via instaloader.
    - User profiles  → Profile.from_username
    - Single posts   → Post.from_shortcode
    - Bulk post URLs → Post.from_shortcode per URL
    """

    # ------------------------------------------------------------------ #
    #  Public entry points                                                 #
    # ------------------------------------------------------------------ #

    def fetch_user_posts(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        logger.info(f"[instaloader] Fetching profile: @{username}")
        L = _make_loader()

        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except instaloader.exceptions.ProfileNotExistsException:
            logger.error(f"Profile @{username} not found.")
            sys.exit(1)

        if profile.is_private:
            logger.error(f"@{username} is a private account.")
            sys.exit(1)

        # For last_n we need all posts; for first_n only, stop early.
        fetch_limit = first_n if (last_n == 0 and first_n > 0) else None

        items = []
        for post in profile.get_posts():
            item = self._convert_instaloader(post)
            if item:
                items.append(item)
            if fetch_limit and len(items) >= fetch_limit:
                break

        logger.info(f"  Got {len(items)} posts from @{username}.")
        return self._slice(items, first_n, last_n)

    # ------------------------------------------------------------------ #
    #  instaloader post → pipeline media_item format                      #
    # ------------------------------------------------------------------ #

    def _convert_instaloader(self, post) -> dict | None:
        shortcode = post.shortcode
        caption   = post.caption or ""
        username  = post.owner_username

        typename = post.typename  # GraphImage | GraphVideo | GraphSidecar

        if typename == "GraphSidecar":
            children = []
            for idx, node in enumerate(post.get_sidecar_nodes()):
                if node.is_video:
                    child = {
                        "code":            f"{shortcode}_{idx}",
                        "media_type":      2,
                        "video_versions":  [{"url": node.video_url}],
                        "image_versions2": {"candidates": [{"url": node.display_url}]},
                    }
                else:
                    child = {
                        "code":            f"{shortcode}_{idx}",
                        "media_type":      1,
                        "image_versions2": {"candidates": [{"url": node.display_url}]},
                    }
                children.append(child)
            if not children:
                return None
            return {
                "code":           shortcode,
                "media_type":     8,
                "carousel_media": children,
                "caption":        {"text": caption},
                "user":           {"username": username},
            }

        if typename == "GraphVideo":
            return {
                "code":            shortcode,
                "media_type":      2,
                "video_versions":  [{"url": post.video_url}],
                "image_versions2": {"candidates": [{"url": post.url}]},
                "caption":         {"text": caption},
                "user":            {"username": username},
            }

        # GraphImage (and any unknown type → treat as image)
        return {
            "code":            shortcode,
            "media_type":      1,
            "image_versions2": {"candidates": [{"url": post.url}]},
            "caption":         {"text": caption},
            "user":            {"username": username},
        }

    def fetch_single_post(self, url_or_shortcode: str) -> list:
        shortcode = self._extract_shortcode(url_or_shortcode)
        logger.info(f"[instaloader] Fetching post: {shortcode}")
        L = _make_loader()
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
        except Exception as e:
            logger.warning(f"Could not fetch {shortcode}: {e}")
            return []
        item = self._convert_instaloader(post)
        return [item] if item else []

    def fetch_bulk(self, urls: list) -> list:
        media_items = []
        L = _make_loader()  # reuse one context for all posts
        for url in urls:
            time.sleep(random.uniform(1, 3))
            shortcode = self._extract_shortcode(url)
            try:
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                item = self._convert_instaloader(post)
                if item:
                    media_items.append(item)
            except Exception as e:
                logger.warning(f"Skipping {url}: {e}")
        return media_items

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_shortcode(url_or_shortcode: str) -> str:
        m = _SC_RE.search(url_or_shortcode)
        if m:
            return m.group(1)
        # Bare shortcode passed directly
        return url_or_shortcode.strip("/")

    @staticmethod
    def _slice(items: list, first_n: int, last_n: int) -> list:
        if last_n > 0 and len(items) > (first_n + last_n):
            first_part = items[:first_n] if first_n > 0 else []
            last_part  = items[-last_n:]
            seen, out  = set(), []
            for item in first_part + last_part:
                code = item.get("code")
                if code and code not in seen:
                    seen.add(code)
                    out.append(item)
            return out
        if first_n > 0:
            return items[:first_n]
        return items
