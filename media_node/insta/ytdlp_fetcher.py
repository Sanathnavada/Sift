import logging
import random
import re
import time
import json
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import instaloader
except ImportError:
    instaloader = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


_SC_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,20}$")
_VALID_SCRAPING_PATHS = {"instaloader", "ytdlp", "playwright"}
_SESSION_FILE = "web_session.json"
_YTDLP_COOKIE_FILE = "yt_dlp_cookies.txt"
_COOKIE_NAMES = {"sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "datr", "rur"}
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


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
    Fetch public Instagram content through a selectable backend.

    instaloader returns direct media URLs. yt-dlp returns pipeline-compatible
    media items that are downloaded by yt-dlp later. playwright enumerates
    profile post URLs in a real browser, then reuses the yt-dlp post path.
    """

    def __init__(self, scraping_path: str = "instaloader", session_dir: str | None = None):
        scraping_path = (scraping_path or "instaloader").strip().lower()
        if scraping_path not in _VALID_SCRAPING_PATHS:
            logger.warning("Unknown SCRAPING_PATH=%s; using instaloader.", scraping_path)
            scraping_path = "instaloader"
        self.scraping_path = scraping_path
        self.session_file = Path(session_dir) / _SESSION_FILE if session_dir else None

        if self.scraping_path == "instaloader" and instaloader is None:
            logger.error("instaloader not installed. Run: pip install instaloader")
            raise RuntimeError("instaloader not installed. Run: pip install instaloader")
        if self.scraping_path in {"ytdlp", "playwright"} and yt_dlp is None:
            logger.error("yt-dlp not installed. Run: pip install yt-dlp")
            raise RuntimeError("yt-dlp not installed. Run: pip install yt-dlp")
        if self.scraping_path == "playwright" and sync_playwright is None:
            logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
            raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

    def fetch_user_posts(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        if self.scraping_path == "playwright":
            return self._fetch_user_posts_playwright(username, first_n, last_n)
        if self.scraping_path == "ytdlp":
            return self._fetch_user_posts_ytdlp(username, first_n, last_n)
        return self._fetch_user_posts_instaloader(username, first_n, last_n)

    def fetch_single_post(self, url_or_shortcode: str) -> list:
        if self.scraping_path == "playwright":
            return self._fetch_single_post_playwright(url_or_shortcode)
        if self.scraping_path == "ytdlp":
            return self._fetch_single_post_ytdlp(url_or_shortcode)
        return self._fetch_single_post_instaloader(url_or_shortcode)

    def fetch_bulk(self, urls: list) -> list:
        if self.scraping_path == "playwright":
            return self._fetch_bulk_playwright(urls)
        if self.scraping_path == "ytdlp":
            return self._fetch_bulk_ytdlp(urls)
        return self._fetch_bulk_instaloader(urls)

    def _fetch_user_posts_instaloader(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        logger.info(f"[instaloader] Fetching profile: @{username}")
        L = _make_loader()

        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except instaloader.exceptions.ProfileNotExistsException:
            logger.error(f"Profile @{username} not found.")
            raise RuntimeError(f"Instagram profile @{username} was not found.")

        if profile.is_private:
            logger.error(f"@{username} is a private account.")
            raise RuntimeError(f"Instagram profile @{username} is private.")

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

    def _fetch_single_post_instaloader(self, url_or_shortcode: str) -> list:
        if instaloader is None:
            logger.warning("instaloader is not installed; cannot fetch %s", url_or_shortcode)
            return []
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

    def _fetch_bulk_instaloader(self, urls: list) -> list:
        media_items = []
        L = _make_loader()
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

    def _convert_instaloader(self, post) -> dict | None:
        shortcode = post.shortcode
        caption = post.caption or ""
        username = post.owner_username
        typename = post.typename

        if typename == "GraphSidecar":
            children = []
            for idx, node in enumerate(post.get_sidecar_nodes()):
                if node.is_video:
                    child = {
                        "code": f"{shortcode}_{idx}",
                        "media_type": 2,
                        "video_versions": [{"url": node.video_url}],
                        "image_versions2": {"candidates": [{"url": node.display_url}]},
                    }
                else:
                    child = {
                        "code": f"{shortcode}_{idx}",
                        "media_type": 1,
                        "image_versions2": {"candidates": [{"url": node.display_url}]},
                    }
                children.append(child)
            if not children:
                return None
            return {
                "code": shortcode,
                "media_type": 8,
                "carousel_media": children,
                "caption": {"text": caption},
                "user": {"username": username},
            }

        if typename == "GraphVideo":
            return {
                "code": shortcode,
                "media_type": 2,
                "video_versions": [{"url": post.video_url}],
                "image_versions2": {"candidates": [{"url": post.url}]},
                "caption": {"text": caption},
                "user": {"username": username},
            }

        return {
            "code": shortcode,
            "media_type": 1,
            "image_versions2": {"candidates": [{"url": post.url}]},
            "caption": {"text": caption},
            "user": {"username": username},
        }

    def _fetch_user_posts_ytdlp(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        if last_n > 0:
            logger.warning("yt-dlp profile mode fetches newest posts; last_n is treated as a newest-post limit.")
        limit = first_n or last_n or 0
        url = f"https://www.instagram.com/{username.strip().lstrip('@')}/"
        logger.info(f"[yt-dlp] Fetching profile: {url}")

        with yt_dlp.YoutubeDL(self._ytdlp_opts(playlistend=limit or None)) as ydl:
            info = ydl.extract_info(url, download=False)

        entries = (info or {}).get("entries") or []
        items = [self._convert_ytdlp_info(entry) for entry in entries if entry]
        items = [item for item in items if item]
        logger.info(f"  Got {len(items)} posts from @{username} via yt-dlp.")
        return self._slice(items, first_n, last_n)

    def _fetch_single_post_ytdlp(self, url_or_shortcode: str) -> list:
        url = self._normalize_post_url(url_or_shortcode)
        logger.info(f"[yt-dlp] Fetching post metadata: {url}")
        try:
            with yt_dlp.YoutubeDL(self._ytdlp_opts()) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.warning(f"Could not fetch {url}: {e}")
            return []
        item = self._convert_ytdlp_info(info)
        return [item] if item else []

    def _fetch_bulk_ytdlp(self, urls: list) -> list:
        media_items = []
        for url in urls:
            time.sleep(random.uniform(1, 3))
            media_items.extend(self._fetch_single_post_ytdlp(url))
        return media_items

    def _fetch_single_post_playwright(self, url_or_shortcode: str) -> list:
        url = self._normalize_post_url(url_or_shortcode)
        shortcode = self._extract_shortcode(url)
        logger.info(f"[playwright] Fetching post metadata: {url}")
        items = self._extract_post_media_with_playwright(url, shortcode)
        if items:
            return items

        # Pure reels/TV posts are a strong fit for yt-dlp. Feed /p/ URLs can be
        # images or mixed sidecar carousels, so try the carousel-aware
        # instaloader path before treating the parent URL as a video download.
        if self._is_reel_or_tv_url(url):
            logger.warning("Playwright did not expose post JSON for %s; trying yt-dlp with saved session cookies.", url)
            items = self._fetch_single_post_ytdlp(url)
            if items:
                return items

        logger.warning("Playwright did not expose post JSON for %s; falling back to instaloader.", url)
        items = self._fetch_single_post_instaloader(url)
        if items:
            return items

        # Do not use parent-url yt-dlp as a final fallback for /p/ feed posts.
        # It works for pure reels, but mixed carousel/feed URLs commonly report
        # "No video formats found" and bypass the existing carousel child path.
        logger.warning("Could not fetch post media for %s", url)
        return []

    def _fetch_bulk_playwright(self, urls: list) -> list:
        media_items = []
        for url in urls:
            time.sleep(random.uniform(1, 3))
            media_items.extend(self._fetch_single_post_playwright(url))
        return media_items

    def _fetch_user_posts_playwright(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        if last_n > 0:
            logger.warning("Playwright profile mode fetches newest posts; last_n is treated as a newest-post limit.")

        limit = first_n or last_n or 0
        profile_url = f"https://www.instagram.com/{username.strip().lstrip('@')}/"
        logger.info(f"[playwright] Enumerating profile posts: {profile_url}")
        items = self._enumerate_profile_media_with_playwright(profile_url, limit=limit)
        logger.info(f"  Discovered {len(items)} post(s) from @{username}.")
        return self._slice(items, first_n, last_n)

    def _enumerate_profile_media_with_playwright(self, profile_url: str, limit: int = 0) -> list[dict]:
        seen = set()
        items = []
        fallback_urls = []

        def add_item(item: dict) -> None:
            code = item.get("code")
            if code and code not in seen:
                seen.add(code)
                items.append(item)

        def add_url(url: str) -> None:
            shortcode = self._extract_shortcode(url)
            if shortcode and shortcode not in seen and url not in fallback_urls:
                fallback_urls.append(url)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_BROWSER_UA)
            self._restore_browser_cookies(context)
            page = context.new_page()

            def on_response(response) -> None:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return
                data = self._safe_response_json(response)
                if data is None:
                    return
                for item in self._find_media_items(data):
                    add_item(item)

            page.on("response", on_response)
            page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)

            stall = 0
            previous_count = 0
            while stall < 4:
                for href in self._extract_post_hrefs(page):
                    add_url(href)

                if limit and len(items) >= limit:
                    break

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2_000)
                if len(items) == previous_count:
                    stall += 1
                else:
                    stall = 0
                previous_count = len(items)

            page.remove_listener("response", on_response)

        if limit:
            fallback_urls = fallback_urls[:max(limit - len(items), 0)]
        for url in fallback_urls:
            for item in self._fetch_single_post_playwright(url):
                add_item(item)
        return items[:limit] if limit else items

    def _enumerate_profile_urls_with_playwright(self, profile_url: str, limit: int = 0) -> list[str]:
        seen = set()
        ordered = []
        stall = 0
        previous_count = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_BROWSER_UA)
            self._restore_browser_cookies(context)
            page = context.new_page()

            def add_url(url: str) -> None:
                shortcode = self._extract_shortcode(url)
                if shortcode and shortcode not in seen:
                    seen.add(shortcode)
                    ordered.append(url)

            def on_response(response) -> None:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return
                data = self._safe_response_json(response)
                if data is None:
                    return
                for shortcode in self._find_shortcodes(data):
                    add_url(f"https://www.instagram.com/p/{shortcode}/")

            page.on("response", on_response)
            page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)

            while stall < 4:
                for href in self._extract_post_hrefs(page):
                    add_url(href)
                    if limit and len(ordered) >= limit:
                        break

                if limit and len(ordered) >= limit:
                    break

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2_000)
                if limit and len(ordered) >= limit:
                    break
                if len(ordered) == previous_count:
                    stall += 1
                else:
                    stall = 0
                previous_count = len(ordered)

            page.remove_listener("response", on_response)

        return ordered[:limit] if limit else ordered

    @staticmethod
    def _is_reel_or_tv_url(url: str) -> bool:
        return "/reel/" in url or "/tv/" in url

    @staticmethod
    def _is_carousel_item(item: dict) -> bool:
        return item.get("media_type") == 8 or bool(item.get("carousel_media"))

    @classmethod
    def _media_item_score(cls, item: dict) -> tuple[int, int]:
        """Rank duplicate media candidates for the same shortcode.

        Instagram's single-post pages often emit several JSON fragments for the
        same shortcode. Prefer the rich sidecar parent over a preview/video-ish
        fragment so existing carousel processing receives the children.
        """
        child_count = len(item.get("carousel_media") or [])
        if cls._is_carousel_item(item):
            return (3000 + child_count, child_count)
        if item.get("media_type") == 2 or item.get("video_versions"):
            return (2000, len(item.get("video_versions") or []))
        if item.get("media_type") == 1 or item.get("image_versions2"):
            return (1000, len((item.get("image_versions2") or {}).get("candidates") or []))
        return (0, 0)

    @classmethod
    def _select_best_media_candidates(cls, candidates: list[dict], shortcode: str) -> list[dict]:
        matching = [item for item in candidates if item.get("code") == shortcode]
        if not matching:
            return []
        matching.sort(key=cls._media_item_score, reverse=True)
        return [matching[0]]

    def _extract_post_media_with_playwright(self, url: str, shortcode: str) -> list[dict]:
        candidates = []

        def add_item(item: dict) -> None:
            if item.get("code") == shortcode:
                candidates.append(item)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_BROWSER_UA)
            self._restore_browser_cookies(context)
            page = context.new_page()

            def on_response(response) -> None:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return
                data = self._safe_response_json(response)
                if data is None:
                    return
                for item in self._find_media_items(data):
                    add_item(item)

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    # Instagram often keeps long-polling resources open. A networkidle
                    # timeout is not a failure; continue with the JSON/DOM fallbacks.
                    pass
                page.wait_for_timeout(3_000)

                selected = self._select_best_media_candidates(candidates, shortcode)
                if selected:
                    return selected

                for item in self._extract_post_api_fallback(page, shortcode):
                    add_item(item)
                selected = self._select_best_media_candidates(candidates, shortcode)
                if selected:
                    return selected

                for item in self._extract_post_embedded_json_fallback(page, shortcode):
                    add_item(item)
                selected = self._select_best_media_candidates(candidates, shortcode)
                if selected:
                    return selected

                for item in self._extract_post_dom_fallback(page, url, shortcode):
                    add_item(item)
                return self._select_best_media_candidates(candidates, shortcode)
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

    def _extract_post_api_fallback(self, page, shortcode: str) -> list[dict]:
        """Fetch Instagram's authenticated shortcode info endpoint in-page.

        The direct /p/ page sometimes does not emit usable post JSON before the
        network listener is removed. Calling the same-origin web API from the
        logged-in Playwright page keeps cookies/CSRF/session handling inside the
        browser and returns the rich media object for sidecar carousel posts.
        """
        try:
            payload = page.evaluate(
                """
                async (shortcode) => {
                  const csrf = document.cookie
                    .split('; ')
                    .find((part) => part.startsWith('csrftoken='))
                    ?.split('=')[1] || '';
                  const response = await fetch(`/api/v1/media/shortcode/${shortcode}/info/`, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                      'Accept': 'application/json, text/plain, */*',
                      'X-Requested-With': 'XMLHttpRequest',
                      'X-IG-App-ID': '936619743392459',
                      ...(csrf ? {'X-CSRFToken': csrf} : {})
                    }
                  });
                  const text = await response.text();
                  if (!response.ok) {
                    return {ok: false, status: response.status, body: text.slice(0, 500)};
                  }
                  try {
                    return {ok: true, data: JSON.parse(text)};
                  } catch (error) {
                    return {ok: false, status: response.status, body: text.slice(0, 500)};
                  }
                }
                """,
                shortcode,
            )
        except Exception as exc:
            logger.debug("Instagram shortcode API fallback failed for %s: %s", shortcode, exc)
            return []

        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.debug(
                "Instagram shortcode API fallback did not return media for %s: %s",
                shortcode,
                payload,
            )
            return []

        data = payload.get("data")
        items = self._find_media_items(data)
        selected = self._select_best_media_candidates(items, shortcode)
        if selected:
            logger.info("[playwright] Fetched post metadata through authenticated shortcode API: %s", shortcode)
        return selected

    def _extract_post_embedded_json_fallback(self, page, shortcode: str) -> list[dict]:
        """Parse embedded script JSON when network response capture is empty."""
        try:
            scripts = page.evaluate(
                """
                (shortcode) => Array.from(document.scripts)
                  .map((script) => ({
                    type: script.type || '',
                    text: script.textContent || ''
                  }))
                  .filter((script) => script.text.includes(shortcode))
                  .slice(0, 80)
                """,
                shortcode,
            )
        except Exception as exc:
            logger.debug("Could not read Instagram embedded JSON scripts for %s: %s", shortcode, exc)
            return []

        candidates = []
        for script in scripts or []:
            text = (script or {}).get("text") or ""
            stripped = text.strip()
            if not stripped or stripped[0] not in "[{":
                continue
            try:
                data = json.loads(stripped)
            except Exception:
                continue
            candidates.extend(self._find_media_items(data))

        selected = self._select_best_media_candidates(candidates, shortcode)
        if selected:
            logger.info("[playwright] Fetched post metadata from embedded page JSON: %s", shortcode)
        return selected


    def _read_saved_cookies(self) -> dict:
        if not self.session_file or not self.session_file.exists():
            return {}
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if key in _COOKIE_NAMES and value
        }

    def _ensure_ytdlp_cookiefile(self) -> str | None:
        """Write saved Instagram browser cookies in Netscape format for yt-dlp.

        Playwright can reuse cookies directly, but yt-dlp expects a browser or
        Netscape cookie file. Without this, private/session-bound reels often
        fail with Instagram's "empty media response" even after the UI says the
        session is connected.
        """
        cookies = self._read_saved_cookies()
        if not cookies or not self.session_file:
            return None

        cookie_path = self.session_file.with_name(_YTDLP_COOKIE_FILE)
        lines = [
            "# Netscape HTTP Cookie File",
            "# Generated from the saved Instagram web session.",
        ]
        # Use a far-future expiry. The real session validity is still governed
        # by Instagram; this only makes the cookie jar acceptable to yt-dlp.
        expiry = "2147483647"
        for name, value in cookies.items():
            secure = "TRUE" if name in {"sessionid", "csrftoken", "ds_user_id"} else "FALSE"
            lines.append(f".instagram.com\tTRUE\t/\t{secure}\t{expiry}\t{name}\t{value}")
            lines.append(f"www.instagram.com\tFALSE\t/\t{secure}\t{expiry}\t{name}\t{value}")

        try:
            cookie_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                cookie_path.chmod(0o600)
            except OSError:
                pass
            return str(cookie_path)
        except Exception as exc:
            logger.debug("Could not write yt-dlp Instagram cookie file: %s", exc)
            return None

    @staticmethod
    def _safe_response_json(response) -> dict | list | None:
        try:
            return response.json()
        except BaseException as exc:
            # Playwright can cancel in-flight response bodies when the page or
            # context closes. In Python 3.12 asyncio.CancelledError is not caught
            # by `except Exception`, so handle it explicitly and keep shutdown
            # clean instead of emitting noisy callback tracebacks.
            if exc.__class__.__name__ in {"CancelledError", "TargetClosedError"}:
                return None
            if "Target page, context or browser has been closed" in str(exc):
                return None
            logger.debug("Could not decode Instagram response JSON: %s", exc)
            return None

    def _build_ytdlp_download_item(
        self,
        url: str,
        shortcode: str,
        *,
        caption: str = "",
        thumbnail_url: str | None = None,
        username: str = "unknown",
    ) -> dict:
        item = {
            "code": shortcode,
            "media_type": 2,
            "product_type": "clips" if "/reel/" in url else "feed",
            "video_versions": [{"url": url}],
            "caption": {"text": caption or ""},
            "user": {"username": username or "unknown"},
            "_download_with_ytdlp": True,
            "_source_url": url,
        }
        if thumbnail_url:
            item["image_versions2"] = {"candidates": [{"url": thumbnail_url}]}
        cookiefile = self._ensure_ytdlp_cookiefile()
        if cookiefile:
            item["_ytdlp_cookiefile"] = cookiefile
        return item

    def _extract_post_dom_fallback(self, page, url: str, shortcode: str) -> list[dict]:
        """Return a conservative DOM fallback when rich JSON is unavailable.

        Keep yt-dlp parent-URL fallback for reels/TV posts, where the parent URL
        is normally a single video. For /p/ feed URLs, avoid manufacturing a
        video item from the parent page because mixed sidecar carousels often do
        not have a single downloadable parent video format.
        """
        try:
            payload = page.evaluate(
                """
                () => {
                  const pick = (selector) => {
                    const node = document.querySelector(selector);
                    return node ? (node.getAttribute('content') || node.getAttribute('href') || '') : '';
                  };
                  return {
                    url: window.location.href,
                    title: document.title || '',
                    canonical: pick('link[rel="canonical"]'),
                    description: pick('meta[property="og:description"], meta[name="description"]'),
                    image: pick('meta[property="og:image"]'),
                    video: pick('meta[property="og:video"], meta[property="og:video:secure_url"]'),
                    text: document.body ? document.body.innerText.slice(0, 2000) : ''
                  };
                }
                """
            )
        except Exception:
            return []

        page_url = (payload or {}).get("url") or ""
        canonical = (payload or {}).get("canonical") or ""
        combined = "\n".join(str((payload or {}).get(key) or "") for key in ("url", "canonical", "title", "description", "text"))
        if shortcode not in page_url and shortcode not in canonical and shortcode not in combined:
            return []
        lowered = combined.lower()
        if "/accounts/login" in page_url.lower() or "log in to instagram" in lowered:
            return []

        source_url = canonical if _SC_RE.search(canonical) else url
        caption = (payload or {}).get("description") or (payload or {}).get("title") or ""
        image_url = (payload or {}).get("image") or None
        video_url = (payload or {}).get("video") or None

        if self._is_reel_or_tv_url(source_url):
            return [
                self._build_ytdlp_download_item(
                    source_url,
                    shortcode,
                    caption=caption,
                    thumbnail_url=image_url,
                )
            ]

        if video_url:
            item = {
                "code": shortcode,
                "media_type": 2,
                "product_type": "feed",
                "video_versions": [{"url": video_url}],
                "caption": {"text": caption},
                "user": {"username": "unknown"},
            }
            if image_url:
                item["image_versions2"] = {"candidates": [{"url": image_url}]}
            return [item]

        # Do not turn a /p/ page's og:image preview into a completed post here.
        # For mixed carousels that preview is usually only the first child, and
        # returning it would bypass the existing sidecar/child extraction path.
        return []

    def _restore_browser_cookies(self, context) -> None:
        saved_cookies = self._read_saved_cookies()
        cookies = [
            {"name": key, "value": value, "domain": ".instagram.com", "path": "/"}
            for key, value in saved_cookies.items()
        ]
        if cookies:
            context.add_cookies(cookies)

    @staticmethod
    def _extract_post_hrefs(page) -> list[str]:
        hrefs = page.eval_on_selector_all(
            'a[href^="/p/"], a[href^="/reel/"], a[href^="/tv/"]',
            "links => links.map(link => link.href)",
        )
        return [href for href in hrefs if _SC_RE.search(href)]

    @classmethod
    def _find_shortcodes(cls, value) -> list[str]:
        found = []

        def walk(node):
            if isinstance(node, dict):
                shortcode = node.get("shortcode") or node.get("code")
                if isinstance(shortcode, str) and _SHORTCODE_RE.match(shortcode):
                    found.append(shortcode)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

    def _find_media_items(self, value) -> list[dict]:
        found = []

        def walk(node):
            if isinstance(node, dict):
                item = self._normalize_web_media(node)
                if item:
                    found.append(item)
                    return
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

    def _normalize_web_media(self, media: dict) -> dict | None:
        code = media.get("code") or media.get("shortcode")
        if not isinstance(code, str) or not _SHORTCODE_RE.match(code):
            return None

        carousel_children = media.get("carousel_media")
        if not carousel_children and isinstance(media.get("edge_sidecar_to_children"), dict):
            carousel_children = [
                edge.get("node")
                for edge in media["edge_sidecar_to_children"].get("edges", [])
                if isinstance(edge, dict) and edge.get("node")
            ]

        has_image = bool(
            media.get("image_versions2")
            or media.get("display_url")
            or media.get("thumbnail_src")
            or media.get("display_resources")
        )
        has_video = bool(media.get("video_versions") or media.get("video_url"))
        has_carousel = bool(carousel_children)
        if not (has_image or has_video or has_carousel):
            return None

        media_type = media.get("media_type")
        if media_type is None:
            if has_carousel:
                media_type = 8
            elif media.get("is_video") or has_video:
                media_type = 2
            else:
                media_type = 1

        caption = ""
        raw_caption = media.get("caption")
        if isinstance(raw_caption, dict):
            caption = raw_caption.get("text") or ""
        elif raw_caption:
            caption = str(raw_caption)
        elif isinstance(media.get("edge_media_to_caption"), dict):
            edges = media["edge_media_to_caption"].get("edges") or []
            if edges and isinstance(edges[0], dict):
                node = edges[0].get("node") or {}
                caption = node.get("text") or ""

        user = "unknown"
        raw_user = media.get("user") or media.get("owner")
        if isinstance(raw_user, dict):
            user = raw_user.get("username") or user

        item = {
            "pk": str(media.get("pk", media.get("id", code))),
            "id": str(media.get("id", "")),
            "code": code,
            "media_type": media_type,
            "product_type": media.get("product_type", "feed"),
            "caption": {"text": caption},
            "user": {"username": user},
        }

        if media.get("image_versions2"):
            item["image_versions2"] = media["image_versions2"]
        elif media.get("display_resources"):
            candidates = [
                {"url": candidate.get("src")}
                for candidate in media.get("display_resources", [])
                if isinstance(candidate, dict) and candidate.get("src")
            ]
            if candidates:
                item["image_versions2"] = {"candidates": candidates}
        elif media.get("display_url"):
            item["image_versions2"] = {"candidates": [{"url": media["display_url"]}]}
        elif media.get("thumbnail_src"):
            item["image_versions2"] = {"candidates": [{"url": media["thumbnail_src"]}]}

        if media.get("video_versions"):
            item["video_versions"] = media["video_versions"]
        elif media.get("video_url"):
            item["video_versions"] = [{"url": media["video_url"]}]

        if media_type == 8 and carousel_children:
            children = []
            for idx, child in enumerate(carousel_children):
                child_media = child.copy() if isinstance(child, dict) else child
                if isinstance(child_media, dict) and not (child_media.get("code") or child_media.get("shortcode")):
                    child_media["code"] = f"{code}_{idx}"
                normalized = self._normalize_web_media(child_media)
                if normalized:
                    children.append(normalized)
            if children:
                item["carousel_media"] = children

        # Do not automatically convert every Playwright video-shaped feed item
        # into a yt-dlp parent URL download. Direct Instagram JSON already carries
        # usable child URLs, and parent /p/ URLs can be mixed sidecar carousels
        # where yt-dlp reports "No video formats found". Explicit yt-dlp items are
        # still created by _build_ytdlp_download_item() and _convert_ytdlp_info().
        return item

    @staticmethod
    def _post_url_for_item(item: dict) -> str:
        code = item.get("code", "")
        if item.get("product_type") == "clips":
            return f"https://www.instagram.com/reel/{code}/"
        return f"https://www.instagram.com/p/{code}/"

    def _ytdlp_opts(self, *, playlistend: int | None = None) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": True,
            "noplaylist": False,
            "extract_flat": False,
            "skip_download": True,
        }
        cookiefile = self._ensure_ytdlp_cookiefile()
        if cookiefile:
            opts["cookiefile"] = cookiefile
        if playlistend:
            opts["playlistend"] = playlistend
        return opts

    def _convert_ytdlp_info(self, info: dict) -> dict | None:
        if not info:
            return None

        webpage_url = info.get("webpage_url") or info.get("original_url") or ""
        shortcode = self._extract_shortcode(webpage_url) if webpage_url else str(info.get("id", "")).strip()
        if not shortcode:
            return None

        thumbnails = [
            {"url": thumb.get("url")}
            for thumb in info.get("thumbnails", [])
            if thumb.get("url")
        ]
        source_url = webpage_url or self._normalize_post_url(shortcode)

        item = {
            "code": shortcode,
            "media_type": 2,
            "product_type": "clips",
            "video_versions": [{"url": source_url}],
            "image_versions2": {"candidates": thumbnails},
            "caption": {"text": info.get("description") or info.get("title") or ""},
            "user": {"username": info.get("uploader") or info.get("channel") or "unknown"},
            "_download_with_ytdlp": True,
            "_source_url": source_url,
        }
        cookiefile = self._ensure_ytdlp_cookiefile()
        if cookiefile:
            item["_ytdlp_cookiefile"] = cookiefile
        return item

    @staticmethod
    def download_with_ytdlp(source_url: str, folder: str, code: str, cookiefile: str | None = None) -> bool:
        if yt_dlp is None:
            logger.warning("yt-dlp is not installed; cannot download %s", source_url)
            return False

        Path(folder).mkdir(parents=True, exist_ok=True)
        outtmpl = str(Path(folder) / f"00_{code}.%(ext)s")
        opts = {
            "quiet": True,
            "no_warnings": False,
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "noplaylist": True,
        }
        if cookiefile and Path(cookiefile).exists():
            opts["cookiefile"] = cookiefile
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([source_url])
            return True
        except Exception as e:
            logger.warning(f"yt-dlp download failed for {source_url}: {e}")
            return False

    @staticmethod
    def _extract_shortcode(url_or_shortcode: str) -> str:
        m = _SC_RE.search(url_or_shortcode)
        if m:
            return m.group(1)
        return url_or_shortcode.strip("/")

    @classmethod
    def _normalize_post_url(cls, url_or_shortcode: str) -> str:
        value = url_or_shortcode.strip()
        if value.startswith("http"):
            return value
        shortcode = cls._extract_shortcode(value)
        return f"https://www.instagram.com/p/{shortcode}/"

    @staticmethod
    def _slice(items: list, first_n: int, last_n: int) -> list:
        if last_n > 0 and len(items) > (first_n + last_n):
            first_part = items[:first_n] if first_n > 0 else []
            last_part = items[-last_n:]
            seen, out = set(), []
            for item in first_part + last_part:
                code = item.get("code")
                if code and code not in seen:
                    seen.add(code)
                    out.append(item)
            return out
        if first_n > 0:
            return items[:first_n]
        return items
