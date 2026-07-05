import os
import json
import time
import random
import logging
import requests
import re
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
    sync_playwright = None
    PlaywrightTimeout = TimeoutError

# Instagram's public web app ID (same across all browser sessions)
_WEB_APP_ID = "936619743392459"
_WEB_BASE   = "https://www.instagram.com"
_API_BASE   = f"{_WEB_BASE}/api/v1"
_SC_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class InstagramLoginCancelled(RuntimeError):
    """Raised when the UI intentionally stops an interactive login attempt."""


def _cancel_requested(should_cancel=None) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _cleanup_stale_auth_browser_processes() -> None:
    """Close leftover auth-only Chrome processes from a previous cancelled popup.

    The user can close the noVNC popup while Playwright is still waiting for
    login. A later fresh auth attempt should not show that old half-filled
    browser window. Limit the cleanup to auth browser profiles created by this
    module so normal scrape browsers are not affected.
    """
    if not _clean_browser_ui_enabled() or not shutil.which("pkill"):
        return
    _run_window_command(["pkill", "-f", "instagram-auth-browser-"], timeout=1.5)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(int(value.strip()), minimum)
    except ValueError:
        return default


def _browser_window_size() -> tuple[int, int]:
    resolution = os.getenv("VNC_RESOLUTION", "1920x1080x24")
    parts = resolution.lower().split("x")

    width = _env_int("INSTAGRAM_BROWSER_WIDTH", 1920, minimum=800)
    height = _env_int("INSTAGRAM_BROWSER_HEIGHT", 1080, minimum=600)

    if len(parts) >= 2:
        try:
            width = max(int(parts[0]), 800)
            height = max(int(parts[1]), 600)
        except ValueError:
            pass

    return width, height


def _clean_browser_ui_enabled() -> bool:
    return _env_bool(
        "INSTAGRAM_BROWSER_CLEAN_UI",
        default=_env_bool("NOVNC_ENABLED", False),
    )


def _chromium_launch_args(login_url: str | None = None) -> list[str]:
    width, height = _browser_window_size()
    clean_ui = _clean_browser_ui_enabled()

    args = [
        f"--window-size={width},{height}",
        "--window-position=0,0",
        "--force-device-scale-factor=1",
        "--high-dpi-support=1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI,ChromeWhatsNewUI,AutofillServerCommunication",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-popup-blocking",
    ]
    if clean_ui:
        # App mode removes tabs/address bar and is more reliable under Xvfb
        # than plain --kiosk when Playwright creates pages after launch.
        if login_url:
            args.append(f"--app={login_url}")
        args.extend([
            "--kiosk",
            "--start-fullscreen",
            "--start-maximized",
        ])
    else:
        args.append("--start-maximized")
    return args


def _run_window_command(command: list[str], timeout: float = 2.0) -> bool:
    if not command or not shutil.which(command[0]):
        return False
    env = os.environ.copy()
    env.setdefault("DISPLAY", os.getenv("DISPLAY", ":99"))
    try:
        subprocess.run(
            command,
            env=env,
            timeout=timeout,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:
        logger.debug("Window command failed %s: %s", command, exc)
        return False


def _xdotool_window_ids() -> list[str]:
    if not shutil.which("xdotool"):
        return []
    env = os.environ.copy()
    env.setdefault("DISPLAY", os.getenv("DISPLAY", ":99"))
    ids: list[str] = []
    # Chrome for Testing can report different WM_CLASS values depending on build.
    for class_name in ("Google-chrome", "google-chrome", "chrome", "Chromium", "chromium"):
        try:
            output = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", "--class", class_name],
                env=env,
                timeout=1.5,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            continue
        for line in output.splitlines():
            window_id = line.strip()
            if window_id and window_id not in ids:
                ids.append(window_id)
    return ids


def _force_remote_browser_window(page=None) -> None:
    """Best-effort resize/fullscreen for the visible Chromium window in noVNC.

    Browser launch flags are not always enough under Xvfb + fluxbox because the
    first visible window may be created before Playwright opens the real page.
    This helper is intentionally best-effort: if wmctrl/xdotool are absent, auth
    still works exactly as before.
    """
    if not _clean_browser_ui_enabled():
        return

    width, height = _browser_window_size()
    try:
        if page is not None:
            page.bring_to_front()
    except Exception:
        pass

    # Give the X window manager a moment to register the Chrome window.
    time.sleep(0.35)

    xdotool_ids = _xdotool_window_ids()
    for window_id in xdotool_ids:
        _run_window_command(["xdotool", "windowactivate", "--sync", window_id])
        _run_window_command(["xdotool", "windowmove", window_id, "0", "0"])
        _run_window_command(["xdotool", "windowsize", window_id, str(width), str(height)])

    if xdotool_ids:
        # F11 removes Chrome's own toolbar and makes Instagram use the full
        # remote display. If already fullscreen, Chrome ignores duplicate window
        # sizing above and the key keeps the most recently activated window.
        _run_window_command(["xdotool", "key", "F11"])
        return

    # wmctrl fallback when xdotool is not available.
    _run_window_command(["wmctrl", "-r", ":ACTIVE:", "-b", "remove,fullscreen,maximized_vert,maximized_horz"])
    _run_window_command(["wmctrl", "-r", ":ACTIVE:", "-e", f"0,0,0,{width},{height}"])
    _run_window_command(["wmctrl", "-r", ":ACTIVE:", "-b", "add,fullscreen"])

def _new_browser_context(browser):
    width, height = _browser_window_size()
    if _clean_browser_ui_enabled():
        return browser.new_context(
            user_agent=_BROWSER_UA,
            viewport={"width": width, "height": height},
            screen={"width": width, "height": height},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )
    return browser.new_context(user_agent=_BROWSER_UA, no_viewport=True)


def _new_clean_auth_context(playwright, login_url: str):
    width, height = _browser_window_size()
    user_data_dir = tempfile.mkdtemp(prefix="instagram-auth-browser-")
    context = playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=False,
        slow_mo=50,
        user_agent=_BROWSER_UA,
        viewport={"width": width, "height": height},
        screen={"width": width, "height": height},
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        args=_chromium_launch_args(login_url),
    )
    return context, user_data_dir


def _prepare_page_for_clean_vnc(page) -> None:
    if not _clean_browser_ui_enabled():
        return
    width, height = _browser_window_size()
    try:
        page.set_viewport_size({"width": width, "height": height})
    except Exception:
        pass
    try:
        page.evaluate("""
            ({ width, height }) => {
                try { window.moveTo(0, 0); } catch (_) {}
                try { window.resizeTo(width, height); } catch (_) {}
                document.documentElement.style.background = '#fff';
                document.body.style.background = '#fff';
            }
        """, {"width": width, "height": height})
    except Exception:
        pass


def _is_login_still_pending_url(url: str) -> bool:
    """Return True for Instagram pages that are still part of login/challenge flow."""
    normalized = (url or "").lower()
    pending_markers = (
        "/accounts/login",
        "/accounts/two_factor",
        "/challenge/",
        "/accounts/password",
        "/accounts/emailsignup",
        "/accounts/signup",
        "/accounts/confirm",
        "/accounts/suspended",
    )
    return any(marker in normalized for marker in pending_markers)


def _context_cookie_map(context) -> dict:
    try:
        return {cookie["name"]: cookie["value"] for cookie in context.cookies()}
    except Exception:
        return {}


def _wait_for_interactive_login(page, context, timeout_seconds: int, should_cancel=None) -> None:
    """Wait until the interactive browser has a usable authenticated session.

    Do not treat every non-/accounts/login URL as success. Instagram often moves
    through two-factor/challenge/onetap pages during login. The old logic could
    return too early and then fail while resolving the username. A session is
    considered complete only after required auth cookies are present and the
    visible page is no longer on a login/challenge URL.
    """
    deadline = time.monotonic() + max(timeout_seconds, 1)
    while time.monotonic() < deadline:
        if _cancel_requested(should_cancel):
            raise InstagramLoginCancelled("Instagram login was cancelled.")

        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""

        cookies = _context_cookie_map(context)
        has_required_cookies = bool(cookies.get("sessionid") and cookies.get("csrftoken"))
        if has_required_cookies and not _is_login_still_pending_url(current_url):
            return

        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)

    raise RuntimeError(f"Instagram login timed out after {timeout_seconds} seconds.")


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
        if sync_playwright is None:
            raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")
        self.outdir    = outdir
        self.username  = username   
        self.password  = password
        
        # Save session in the parent directory so it's shared across collections
        self.session_file = os.path.join(session_dir, self._SESSION_FILE)
        self._rsession = None
    # ------------------------------------------------------------------ #
    #  Browser auth                                                        #
    # ------------------------------------------------------------------ #

    def _close_browser_resources(self, page=None, context=None, browser=None):
        """Best-effort cleanup for Playwright resources before ML processing starts."""
        for resource, name in ((page, "page"), (context, "context"), (browser, "browser")):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception as exc:
                logger.debug("Could not close Instagram browser %s cleanly: %s", name, exc)

    def _get_browser_cookies(self, timeout_seconds: int = 300, should_cancel=None) -> dict:
        """
        Opens a headed Chromium window, navigates to instagram.com, and lets the
        user log in normally (handles 2FA / security challenges interactively).
        Returns the cookies needed for subsequent API calls.
        """
        logger.info("Opening browser for Instagram login — please complete login in the window.")
        _cleanup_stale_auth_browser_processes()

        raw_cookies = []
        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            user_data_dir = None
            try:
                login_url = f"{_WEB_BASE}/accounts/login/"
                if _clean_browser_ui_enabled():
                    context, user_data_dir = _new_clean_auth_context(p, login_url)
                    pages = context.pages
                    page = pages[0] if pages else context.new_page()
                    _prepare_page_for_clean_vnc(page)
                    _force_remote_browser_window(page)
                    if _cancel_requested(should_cancel):
                        raise InstagramLoginCancelled("Instagram login was cancelled.")
                    if login_url not in page.url:
                        page.goto(login_url, wait_until="domcontentloaded")
                else:
                    browser = p.chromium.launch(
                        headless=False,
                        slow_mo=50,
                        args=_chromium_launch_args(),
                    )
                    context = _new_browser_context(browser)
                    page = context.new_page()
                    _prepare_page_for_clean_vnc(page)
                    if _cancel_requested(should_cancel):
                        raise InstagramLoginCancelled("Instagram login was cancelled.")
                    page.goto(login_url, wait_until="domcontentloaded")

                # If Instagram already redirected us (saved session, onetap, etc.)
                # the login form will never appear — skip straight to cookie extraction.
                # Only fill credentials if we're still on the login page.
                try:
                    page.wait_for_selector('input[name="username"]', timeout=5_000)
                    if self.username and self.password:
                        page.fill('input[name="username"]', self.username, timeout=10_000)
                        page.wait_for_timeout(random.randint(500, 1_000))
                        page.fill('input[name="password"]', self.password, timeout=10_000)
                        page.wait_for_timeout(random.randint(300, 700))
                        page.click('button[type="submit"]', timeout=10_000)
                        logger.info("Credentials submitted. Handle any 2FA / challenge in the browser window...")
                    else:
                        logger.info("Login form visible. Complete Instagram login in the browser window.")
                except PlaywrightTimeout:
                    logger.info("Login form skipped (Instagram auto-redirected — already authenticated).")

                _force_remote_browser_window(page)

                try:
                    _wait_for_interactive_login(page, context, timeout_seconds, should_cancel)
                except InstagramLoginCancelled:
                    logger.info("Instagram login browser closed/cancelled before authentication completed.")
                    raise
                except RuntimeError:
                    logger.error("Login timed out while waiting for the interactive browser login.")
                    raise

                logger.info("✓ Instagram auth cookies detected.")
                try:
                    # Landing on the home page gives the DOM/API one more chance
                    # to expose the logged-in username after 2FA/challenge flows.
                    if page and not _is_login_still_pending_url(page.url):
                        page.goto(_WEB_BASE + "/", wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                page.wait_for_timeout(2_000)
                raw_cookies = context.cookies()

                # Resolve the username before closing the browser so the DOM and
                # in-browser authenticated fetch can be used as fallbacks. Do not
                # fail the auth attempt only because Instagram hides the username;
                # valid session cookies are enough to consider auth connected.
                cookies_preview = {c["name"]: c["value"] for c in raw_cookies}
                if not self.username:
                    self.username = self._resolve_username(cookies_preview, page=page, required=False)
            finally:
                self._close_browser_resources(page, context, browser)
                if user_data_dir:
                    try:
                        shutil.rmtree(user_data_dir, ignore_errors=True)
                    except Exception:
                        pass

        # Index cookies by name
        cookies = {c["name"]: c["value"] for c in raw_cookies}
        required = {"sessionid", "csrftoken"}
        missing  = required - cookies.keys()
        if missing:
            logger.error(f"Login may have failed — missing cookies: {missing}")
            raise RuntimeError(f"Instagram login may have failed; missing cookies: {sorted(missing)}")

        if not self.username:
            self.username = self._resolve_username(cookies, required=False)
        if self.username:
            cookies["_ig_username"] = self.username
            logger.info("✓ Browser session established for @%s.", self.username)
        else:
            cookies["_ig_username"] = ""
            logger.warning("✓ Browser session established, but Instagram username was not exposed by the session.")
        return cookies

    def _resolve_username(self, cookies: dict, page=None, required: bool = True) -> str:
        cached = (cookies.get("_ig_username") or "").strip()
        if cached:
            return cached

        # Browser-side authenticated fetch is the most reliable immediately after
        # 2FA because it uses the exact live page session and Instagram headers.
        if page is not None:
            try:
                username = page.evaluate(
                    """async ({ appId }) => {
                        try {
                            const response = await fetch('/api/v1/accounts/current_user/?edit=true', {
                                credentials: 'include',
                                headers: {
                                    'Accept': 'application/json',
                                    'X-IG-App-ID': appId,
                                    'X-Requested-With': 'XMLHttpRequest'
                                }
                            });
                            if (response.ok) {
                                const data = await response.json();
                                const fromApi = data?.user?.username || data?.username || '';
                                if (fromApi) return fromApi;
                            }
                        } catch (_) {}

                        try {
                            const shared = window._sharedData || {};
                            const viewer = shared?.config?.viewer || shared?.entry_data?.ProfilePage?.[0]?.graphql?.user || {};
                            if (viewer?.username) return viewer.username;
                        } catch (_) {}

                        const blocked = new Set([
                            'accounts', 'direct', 'explore', 'reels', 'stories', 'p', 'reel',
                            'tv', 'about', 'developer', 'privacy', 'terms', 'legal', 'web',
                            'challenge', 'emailsignup', 'accounts_center', 'ajax', 'graphql'
                        ]);
                        const links = Array.from(document.querySelectorAll('a[href^="/"]'));
                        for (const link of links) {
                            const value = (link.getAttribute('href') || '').split('/').filter(Boolean)[0];
                            if (value && !blocked.has(value) && /^[A-Za-z0-9._]+$/.test(value)) {
                                return value;
                            }
                        }
                        return '';
                    }""",
                    {"appId": _WEB_APP_ID},
                )
                if username:
                    return username.strip().lstrip("@")
            except Exception as exc:
                logger.debug("Could not resolve Instagram username from live page: %s", exc)

        session = self._make_session(cookies)
        for url in (
            f"{_API_BASE}/accounts/current_user/?edit=true",
            f"{_API_BASE}/accounts/current_user/",
        ):
            try:
                resp = session.get(url, headers={"Accept": "application/json"}, timeout=15)
                if not resp.ok:
                    logger.debug("Instagram username lookup failed: %s %s", resp.status_code, url)
                    continue
                data = resp.json()
                username = (data.get("user") or {}).get("username") or data.get("username")
                if username:
                    return username.strip().lstrip("@")
            except Exception as exc:
                logger.debug("Instagram username lookup failed for %s: %s", url, exc)

        ds_user_id = cookies.get("ds_user_id")
        if ds_user_id:
            try:
                resp = session.get(
                    f"{_API_BASE}/users/{ds_user_id}/info/",
                    headers={"Accept": "application/json"}, timeout=15,
                )
                if resp.ok:
                    data = resp.json()
                    username = (data.get("user") or {}).get("username")
                    if username:
                        return username.strip().lstrip("@")
                else:
                    logger.debug("Instagram user-id username lookup failed: %s", resp.status_code)
            except Exception as exc:
                logger.debug("Instagram user-id username lookup failed: %s", exc)

        if required:
            raise RuntimeError(
                "Could not determine the logged-in Instagram username from the browser session."
            )
        return ""

    def has_saved_session(self) -> bool:
        """Return True when a browser-auth session file exists with required cookies."""
        return os.path.exists(self.session_file) and bool(self._read_saved_cookies())

    def acquire_interactive_login(self, timeout_seconds: int = 300, should_cancel=None) -> dict:
        """
        Open a headed Chromium login session and persist the resulting cookies.

        This is intentionally explicit. Scrape jobs should not surprise-open a
        login browser; the web UI starts this method through a dedicated auth job.
        """
        cookies = self._get_browser_cookies(timeout_seconds=timeout_seconds, should_cancel=should_cancel)
        self._save_cookies(cookies)
        return {
            "username": cookies.get("_ig_username"),
            "session_file": self.session_file,
        }

    def _save_cookies(self, cookies: dict) -> None:
        os.makedirs(os.path.dirname(self.session_file), exist_ok=True)
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(cookies, f)

    def _read_saved_cookies(self) -> dict:
        if not os.path.exists(self.session_file):
            return {}
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        if not data.get("sessionid") or not data.get("csrftoken"):
            return {}
        return data

    def _load_saved_cookies(self) -> dict:
        """Load an existing web session; never opens a login browser implicitly."""
        data = self._read_saved_cookies()
        if not data:
            raise RuntimeError(
                "Instagram login is required. Connect Instagram before running this workflow."
            )

        if not self.username:
            self.username = data.get("_ig_username") or self._resolve_username(data, required=False)
            if self.username and not data.get("_ig_username"):
                data["_ig_username"] = self.username
                self._save_cookies(data)
        logger.info("Reusing cached Instagram browser session.")
        return data

    def _load_or_acquire_cookies(self) -> dict:
        """
        Backward-compatible internal alias.

        Historically this method opened a login browser when no session existed.
        The web app now separates login acquisition from scraping, so scraping
        flows only load an already-saved session.
        """
        return self._load_saved_cookies()

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
            cookies = self._load_saved_cookies()
            self._rsession = self._make_session(cookies)

    # ------------------------------------------------------------------ #
    #  Collection fetching                                                 #
    # ------------------------------------------------------------------ #

    def _api_get(self, path: str, params: dict = None) -> dict:
        url = f"{_API_BASE}/{path.lstrip('/')}"
        resp = self._rsession.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            logger.error("Web session expired mid-run. Delete web_session.json and retry.")
            raise RuntimeError("Instagram web session expired. Delete web_session.json and retry.")
        if not resp.ok:
            # Log the body so we can diagnose unexpected 400s
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:300]
            logger.error(f"[Web API] {resp.status_code} on {url} — {body}")
            resp.raise_for_status()
        return resp.json()

    def fetch_public_profile(self, username: str, first_n: int = 0, last_n: int = 0) -> list:
        self._ensure_session()
        profile_username = username.strip().lstrip("@")
        limit = first_n or last_n or 0
        logger.info(f"[browser] Fetching public profile: @{profile_username}")
        items = self._fetch_public_profile_via_browser(profile_username, limit)
        logger.info(f"Fetched {len(items)} post(s) from @{profile_username} via browser session.")
        return self._slice(items, first_n, last_n)

    def _fetch_public_profile_via_browser(self, username: str, limit: int = 0) -> list:
        with open(self.session_file) as f:
            saved_cookies = json.load(f)

        media_items = []
        fallback_urls = []
        seen_codes: set = set()

        def add_item(item: dict) -> None:
            code = item.get("code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                media_items.append(item)

        def add_url(url: str) -> None:
            match = _SC_RE.search(url)
            if not match:
                return
            shortcode = match.group(1)
            if shortcode not in seen_codes and url not in fallback_urls:
                fallback_urls.append(url)

        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            user_data_dir = None
            try:
                browser = p.chromium.launch(
                    headless=False,
                    slow_mo=50,
                    args=_chromium_launch_args(),
                )
                context = _new_browser_context(browser)
                context.add_cookies([
                    {"name": k, "value": v, "domain": ".instagram.com", "path": "/"}
                    for k, v in saved_cookies.items()
                    if k in {"sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "datr"}
                ])
                page = context.new_page()
                _prepare_page_for_clean_vnc(page)

                def on_response(response):
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type:
                        return
                    try:
                        data = response.json()
                    except Exception:
                        return
                    for item in self._find_media_items(data):
                        add_item(item)

                page.on("response", on_response)
                try:
                    page.goto(f"{_WEB_BASE}/{username}/", wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(3_000)

                    stall = 0
                    prev_count = 0
                    while stall < 4:
                        for href in page.eval_on_selector_all(
                            'a[href^="/p/"], a[href^="/reel/"], a[href^="/tv/"]',
                            "links => links.map(link => link.href)",
                        ):
                            add_url(href)

                        if limit and len(media_items) >= limit:
                            break

                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(2_000)
                        if len(media_items) == prev_count:
                            stall += 1
                        else:
                            stall = 0
                        prev_count = len(media_items)
                finally:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
            finally:
                self._close_browser_resources(page, context, browser)
                if user_data_dir:
                    try:
                        shutil.rmtree(user_data_dir, ignore_errors=True)
                    except Exception:
                        pass

        logger.info("Instagram browser closed before media processing starts.")

        if limit:
            fallback_urls = fallback_urls[:max(limit - len(media_items), 0)]
        if fallback_urls:
            from sift.engines.media.instagram.ytdlp_fetcher import YtdlpInstaFetcher

            fetcher = YtdlpInstaFetcher(
                scraping_path="playwright",
                session_dir=os.path.dirname(self.session_file),
            )
            for url in fallback_urls:
                for item in fetcher.fetch_single_post(url):
                    add_item(item)

        return media_items[:limit] if limit else media_items

    def _find_media_items(self, value) -> list:
        found = []

        def walk(node):
            if isinstance(node, dict):
                item = self._normalize(node)
                if item.get("code") and (
                    item.get("image_versions2")
                    or item.get("video_versions")
                    or item.get("carousel_media")
                ):
                    found.append(item)
                    return
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

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
            raise RuntimeError(f"Instagram collection '{collection_name}' was not found. Check the exact name.")

        logger.info(f"Found collection id: {col_id}")

        if not self.username:
            raise RuntimeError(
                "Instagram session is connected, but the logged-in username could not be resolved. "
                "Refresh Instagram login, then retry the private collection workflow."
            )

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
        seen_codes: set = set()

        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            user_data_dir = None
            try:
                browser = p.chromium.launch(
                    headless=False,
                    slow_mo=50,
                    args=_chromium_launch_args(),
                )
                context = _new_browser_context(browser)
                context.add_cookies([
                    {"name": k, "value": v, "domain": ".instagram.com", "path": "/"}
                    for k, v in saved_cookies.items()
                    if k in {"sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "datr"}
                ])
                page = context.new_page()
                _prepare_page_for_clean_vnc(page)

                def on_response(response):
                    if f"feed/collection/{col_id}" not in response.url:
                        return
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
                        logger.info("  Intercepted %s items (total: %s)", len(batch), len(media_items))
                    except Exception:
                        pass

                page.on("response", on_response)
                try:
                    slug = collection_name.lower().replace(" ", "-")
                    url = f"{_WEB_BASE}/{self.username}/saved/{slug}/{col_id}/"
                    logger.info("Navigating to: %s", url)
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3_000)

                    prev_count = 0
                    stall = 0
                    while stall < 3:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(2_000)
                        if len(media_items) == prev_count:
                            stall += 1
                        else:
                            stall = 0
                        prev_count = len(media_items)
                finally:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
            finally:
                self._close_browser_resources(page, context, browser)
                if user_data_dir:
                    try:
                        shutil.rmtree(user_data_dir, ignore_errors=True)
                    except Exception:
                        pass

        logger.info("Instagram browser closed before media processing starts.")
        logger.info("✓ Fetched %s posts from collection via browser interception.", len(media_items))
        return self._slice(media_items, first_n, last_n)

    # ------------------------------------------------------------------ #
    #  Normalise web API response → existing pipeline format              #
    # ------------------------------------------------------------------ #

    def _normalize(self, m: dict) -> dict:
        """
        The web API returns essentially the same JSON as the mobile API.
        Normalise to the dict format that ScraperService.process_posts expects.
        """
        code = m.get("code") or m.get("shortcode") or ""
        carousel_children = m.get("carousel_media")
        if not carousel_children and isinstance(m.get("edge_sidecar_to_children"), dict):
            carousel_children = [
                edge.get("node")
                for edge in m["edge_sidecar_to_children"].get("edges", [])
                if edge.get("node")
            ]

        mt = m.get("media_type")
        if mt is None:
            if carousel_children:
                mt = 8
            elif m.get("is_video"):
                mt = 2
            else:
                mt = 1

        out = {
            "pk":         str(m.get("pk", m.get("id", ""))),
            "id":         str(m.get("id", "")),
            "code":       code,
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
        elif m.get("display_url"):
            out["image_versions2"] = {"candidates": [{"url": m["display_url"]}]}

        # Videos
        if m.get("video_versions"):
            out["video_versions"] = m["video_versions"]
        elif m.get("video_url"):
            out["video_versions"] = [{"url": m["video_url"]}]

        # Carousels
        if mt == 8 and carousel_children:
            out["carousel_media"] = [self._normalize(child) for child in carousel_children]

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
