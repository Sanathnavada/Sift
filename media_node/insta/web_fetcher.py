import os
import sys
import json
import time
import random
import logging
import requests

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

# Instagram's public web app ID (same across all browser sessions)
_WEB_APP_ID = "936619743392459"
_WEB_BASE   = "https://www.instagram.com"
_API_BASE   = f"{_WEB_BASE}/api/v1"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class WebCollectionFetcher:
    """
    Fetches Instagram saved collections via the WEB API (www.instagram.com/api/v1/)
    instead of the mobile private API (i.instagram.com/api/v1/).

    The web API has completely separate bot detection — it is not affected by
    mobile-API IP challenges. Authentication is done through a real Chromium
    browser (Playwright), so login challenges can be handled interactively.
    """

    # Fixed session filename — no username prefix needed
    _SESSION_FILE = "web_session.json"

    def __init__(self, outdir, session_dir, username=None, password=None):
        self.outdir    = outdir
        self.username  = username   
        self.password  = password
        
        # Save session in the parent directory so it's shared across collections
        self.session_file = os.path.join(session_dir, self._SESSION_FILE)
        self._rsession = None
    # ------------------------------------------------------------------ #
    #  Browser auth                                                        #
    # ------------------------------------------------------------------ #

    def _get_browser_cookies(self) -> dict:
        """
        Opens a headed Chromium window, navigates to instagram.com, and lets the
        user log in normally (handles 2FA / security challenges interactively).
        Returns the cookies needed for subsequent API calls.
        """
        logger.info("Opening browser for Instagram login — please complete login in the window.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=50)
            context = browser.new_context(user_agent=_BROWSER_UA)
            page = context.new_page()

            page.goto(f"{_WEB_BASE}/accounts/login/", wait_until="domcontentloaded")

            # If Instagram already redirected us (saved session, onetap, etc.)
            # the login form will never appear — skip straight to cookie extraction.
            # Only fill credentials if we're still on the login page.
            try:
                page.wait_for_selector('input[name="username"]', timeout=5_000)
                # Login form is visible — fill it automatically
                page.fill('input[name="username"]', self.username, timeout=10_000)
                time.sleep(random.uniform(0.5, 1.0))
                page.fill('input[name="password"]', self.password, timeout=10_000)
                time.sleep(random.uniform(0.3, 0.7))
                page.click('button[type="submit"]', timeout=10_000)
                logger.info("Credentials submitted. Handle any 2FA / challenge in the browser window...")
            except PlaywrightTimeout:
                # Form didn't appear — Instagram already redirected us elsewhere
                logger.info("Login form skipped (Instagram auto-redirected — already authenticated).")

            # Wait until we reach the home feed. User can interact with any
            # 2FA or security challenge that appears in the browser window.
            try:
                page.wait_for_url(lambda url: "/accounts/login/" not in url, timeout=120_000)
            except PlaywrightTimeout:
                logger.error("Login timed out after 2 minutes.")
                browser.close()
                sys.exit(1)

            logger.info("✓ Reached Instagram home feed.")

            # Give Instagram a moment to set all cookies
            time.sleep(2)
            raw_cookies = context.cookies()
            browser.close()

        # Index cookies by name
        cookies = {c["name"]: c["value"] for c in raw_cookies}
        required = {"sessionid", "csrftoken"}
        missing  = required - cookies.keys()
        if missing:
            logger.error(f"Login may have failed — missing cookies: {missing}")
            sys.exit(1)

        # Resolve and store username so we don't need it on future runs
        if not self.username:
            self.username = self._resolve_username(cookies)
        cookies["_ig_username"] = self.username

        logger.info(f"✓ Browser session established for @{self.username}.")
        return cookies

    def _resolve_username(self, cookies: dict) -> str:
        """Ask Instagram's API who we're logged in as."""
        try:
            resp = self._make_session(cookies).get(
                f"{_API_BASE}/accounts/current_user/?edit=true",
                headers={"Accept": "application/json"}, timeout=15,
            )
            return resp.json()["user"]["username"]
        except Exception:
            logger.error("Could not determine logged-in username. Pass --username explicitly.")
            sys.exit(1)

    def _load_or_acquire_cookies(self) -> dict:
        """Load cached web session; open browser if expired or missing."""
        if os.path.exists(self.session_file):
            with open(self.session_file) as f:
                data = json.load(f)
            # Restore username from cache if not supplied on the command line
            if not self.username:
                self.username = data.get("_ig_username")
            # Quick validity probe
            try:
                probe = self._make_session(data).get(
                    f"{_API_BASE}/accounts/current_user/?edit=true",
                    headers={"Accept": "application/json"}, timeout=15,
                )
                if probe.status_code == 200:
                    logger.info(f"✓ Reusing cached web session for @{self.username}.")
                    return data
            except Exception:
                pass
            logger.warning("Cached web session expired — re-opening browser.")

        if not (self.username and self.password):
            logger.error(
                "No cached session found. Run with --username and --password once to log in and cache the session."
            )
            sys.exit(1)

        cookies = self._get_browser_cookies()
        os.makedirs(self.outdir, exist_ok=True)
        with open(self.session_file, "w") as f:
            json.dump(cookies, f)
        return cookies

    # ------------------------------------------------------------------ #
    #  requests.Session factory                                            #
    # ------------------------------------------------------------------ #

    def _make_session(self, cookies: dict) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent":       _BROWSER_UA,
            "X-IG-App-ID":     _WEB_APP_ID,
            "X-CSRFToken":     cookies.get("csrftoken", ""),
            # Required by Instagram's web API for non-auth endpoints
            "X-IG-WWW-Claim":  cookies.get("x-ig-www-claim", "0"),
            "X-ASBD-ID":       "129477",
            "Referer":         f"{_WEB_BASE}/",
            "Accept":          "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          _WEB_BASE,
            "Sec-Fetch-Site":  "same-origin",
            "Sec-Fetch-Mode":  "cors",
            "Sec-Fetch-Dest":  "empty",
        })
        for name, value in cookies.items():
            s.cookies.set(name, value, domain=".instagram.com")
        return s

    def _ensure_session(self):
        if self._rsession is None:
            cookies = self._load_or_acquire_cookies()
            self._rsession = self._make_session(cookies)

    # ------------------------------------------------------------------ #
    #  Collection fetching                                                 #
    # ------------------------------------------------------------------ #

    def _api_get(self, path: str, params: dict = None) -> dict:
        url = f"{_API_BASE}/{path.lstrip('/')}"
        resp = self._rsession.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            logger.error("Web session expired mid-run. Delete web_session.json and retry.")
            sys.exit(1)
        if not resp.ok:
            # Log the body so we can diagnose unexpected 400s
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:300]
            logger.error(f"[Web API] {resp.status_code} on {url} — {body}")
            resp.raise_for_status()
        return resp.json()

    def fetch_collection(self, collection_name: str, first_n: int = 0, last_n: int = 0) -> list:
        # Step 1: use requests to get the collection ID (this endpoint works fine)
        self._ensure_session()
        logger.info(f"[Web API] Fetching collection: '{collection_name}'")

        col_id = None
        max_id = ""
        while True:
            data = self._api_get("collections/list/", params={
                "collection_types": '["ALL_MEDIA_AUTO_COLLECTION","PRODUCT_AUTO_COLLECTION","MEDIA"]',
                "max_id": max_id,
            })
            for item in data.get("items", []):
                if item.get("collection_name") == collection_name:
                    col_id = item.get("collection_id")
                    break
            if col_id:
                break
            max_id = data.get("next_max_id")
            if not max_id:
                break

        if not col_id:
            logger.error(f"❌ Collection '{collection_name}' not found. Check name (case-sensitive).")
            sys.exit(1)

        logger.info(f"Found collection id: {col_id}")

        # Step 2: use Playwright to navigate to the collection page and intercept
        # the feed API responses. The browser handles all dynamic headers
        # (X-IG-WWW-Claim, claim tokens, etc.) that requests cannot replicate.
        return self._fetch_via_browser(collection_name, col_id, first_n, last_n)

    def _fetch_via_browser(self, collection_name: str, col_id: str,
                           first_n: int, last_n: int) -> list:
        """Navigate to the saved collection page and intercept the feed responses."""
        with open(self.session_file) as f:
            saved_cookies = json.load(f)

        media_items = []
        seen_codes: set = set()  # dedup guard — browser can fire same response twice

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=50)
            context = browser.new_context(user_agent=_BROWSER_UA)

            # Restore the authenticated session into the browser context
            context.add_cookies([
                {"name": k, "value": v, "domain": ".instagram.com", "path": "/"}
                for k, v in saved_cookies.items()
                if k in {"sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "datr"}
            ])

            page = context.new_page()

            def on_response(response):
                if f"feed/collection/{col_id}" in response.url:
                    try:
                        data = response.json()
                        batch = []
                        for item in data.get("items", []):
                            media = item.get("media") or item
                            if media:
                                normalized = self._normalize(media)
                                code = normalized.get("code")
                                if code and code not in seen_codes:
                                    seen_codes.add(code)
                                    batch.append(normalized)
                        media_items.extend(batch)
                        logger.info(f"  Intercepted {len(batch)} items (total: {len(media_items)})")
                    except Exception:
                        pass

            page.on("response", on_response)

            # Navigate to the saved collection. Instagram uses the collection name
            # as the URL slug; spaces → hyphens, lowercase.
            slug = collection_name.lower().replace(" ", "-")
            url  = f"{_WEB_BASE}/{self.username}/saved/{slug}/{col_id}/"
            logger.info(f"Navigating to: {url}")
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(3)

            # Scroll to load all items
            prev_count = 0
            stall = 0
            while stall < 3:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                if len(media_items) == prev_count:
                    stall += 1
                else:
                    stall = 0
                prev_count = len(media_items)

            browser.close()

        logger.info(f"✓ Fetched {len(media_items)} posts from collection via browser interception.")
        return self._slice(media_items, first_n, last_n)

    # ------------------------------------------------------------------ #
    #  Normalise web API response → existing pipeline format              #
    # ------------------------------------------------------------------ #

    def _normalize(self, m: dict) -> dict:
        """
        The web API returns essentially the same JSON as the mobile API.
        Normalise to the dict format that ScraperService.process_posts expects.
        """
        mt = m.get("media_type", 1)
        out = {
            "pk":         str(m.get("pk", m.get("id", ""))),
            "id":         str(m.get("id", "")),
            "code":       m.get("code", ""),
            "media_type": mt,
            "product_type": m.get("product_type", "feed"),
        }
        if m.get("caption"):
            out["caption"] = {"text": m["caption"].get("text", "") if isinstance(m["caption"], dict) else str(m["caption"])}
        if m.get("user"):
            out["user"] = {"username": m["user"].get("username", "")}

        # Images
        if m.get("image_versions2"):
            out["image_versions2"] = m["image_versions2"]

        # Videos
        if m.get("video_versions"):
            out["video_versions"] = m["video_versions"]

        # Carousels
        if mt == 8 and m.get("carousel_media"):
            out["carousel_media"] = [self._normalize(child) for child in m["carousel_media"]]

        return out

    def _slice(self, items: list, first_n: int, last_n: int) -> list:
        if last_n > 0 and len(items) > (first_n + last_n):
            first_slice = items[:first_n] if first_n > 0 else []
            last_slice  = items[-last_n:] if last_n  > 0 else []
            seen, out   = set(), []
            for item in first_slice + last_slice:
                code = item.get("code")
                if code and code not in seen:
                    seen.add(code)
                    out.append(item)
            return out
        if first_n > 0:
            return items[:first_n]
        return items
