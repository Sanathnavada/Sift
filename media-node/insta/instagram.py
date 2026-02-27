import os
import sys
import time
import json
import random
import logging
import requests
from functools import wraps
from utils import ensure_dir, extract_shortcode

logger = logging.getLogger(__name__)

try:
    from instagrapi import Client
    from instagrapi.exceptions import (
        LoginRequired, TwoFactorRequired,
        ChallengeRequired, CollectionNotFound, RateLimitError
    )
    logging.getLogger("instagrapi").setLevel(logging.ERROR)
except ImportError:
    logger.error("Error: instagrapi not installed. Run: pip install instagrapi")
    sys.exit(1)

try:
    import pyotp
except ImportError:
    pyotp = None

def api_safeguard(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (requests.exceptions.JSONDecodeError, json.decoder.JSONDecodeError):
            logger.error("\n" + "="*70)
            logger.error("🛑 CRITICAL: INSTAGRAM API INTERCEPTED THE REQUEST 🛑")
            sys.exit(1)
        except RateLimitError:
            logger.error("⏳ Rate Limit Exceeded.")
            sys.exit(1)
        except ChallengeRequired:
            logger.error(
                "\n" + "="*70 + "\n"
                "🛑 INSTAGRAM CHECKPOINT — Every API call is being blocked.\n"
                "This is an IP trust issue, not a code issue.\n\n"
                "Resolution steps:\n"
                "  1. Open the Instagram app on your phone.\n"
                "  2. Dismiss the 'Was this you?' / 'Confirm your identity' prompt.\n"
                "  3. Wait 15–30 minutes before retrying.\n"
                "  4. If it persists, use --proxy with a residential IP.\n"
                + "="*70
            )
            sys.exit(1)
        except CollectionNotFound as e:
            logger.error(f"❌ Collection not found: {e}\n  Check the name is correct (case-sensitive) and the collection exists.")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ API Exception in {func.__name__}: {str(e)}")
            raise
    return wrapper

class InstagramFetcher:
    def __init__(self, outdir, username=None, password=None, totp_secret=None, proxy=None, sessionid=None):
        self.cl = Client()

        # Patch login_flow BEFORE any login attempt.
        # Instagram frequently returns a 400 challenge after 2FA on automated clients.
        # instagrapi's default login_flow() crashes on the empty/HTML challenge response.
        # We wrap it so auth succeeds even if the post-login probe endpoints are blocked.
        self._patch_login_flow()

        # Keep device spoofing just to be safe
        self.cl.set_device({
            "app_version": "282.0.0.22.119", "android_version": 33,
            "android_release": "13", "dpi": "480dpi", "resolution": "1080x2316",
            "manufacturer": "samsung", "device": "SM-S908U", "model": "Galaxy S22 Ultra",
            "cpu": "qcom", "version_code": "314665256"
        })
        self.cl.timezone_offset = random.choice([19800, -14400, -18000])

        self.outdir = outdir
        self.session_file = os.path.join(outdir, f"{username}_session.json") if username else os.path.join(outdir, "injected_session.json")
        self.authenticated = False
        self.totp_secret = totp_secret
        self.cl.request_timeout = 30

        if proxy:
            self.cl.set_proxy(proxy)

        if sessionid:
            self._login_with_session(sessionid)
        elif username and password:
            self._smart_login(username, password)

    def _handle_exception(self, _, exc):
        """
        Registered as cl.handle_exception.

        instagrapi's private_request() calls this instead of challenge_resolve()
        whenever an exception is raised inside _send_private_request.  The default
        path calls challenge_resolve_contact_form() which tries result.json() on
        an empty/HTML body → JSONDecodeError crash.

        By re-raising we let the exception propagate cleanly out of private_request
        so our own api_safeguard decorator can handle it properly.
        """
        raise exc

    def _patch_login_flow(self):
        """
        Two patches applied before any login attempt:

        1. cl.handle_exception — re-raises instead of calling challenge_resolve(),
           preventing the JSONDecodeError crash on every blocked API call.

        2. cl.login_flow — wrapped so post-login probe failures (reels_tray,
           timeline) are non-fatal; the 2FA auth token has already been committed
           at this point.
        """
        # Patch 1: bypass the broken challenge auto-resolver for ALL API calls
        self.cl.handle_exception = self._handle_exception

        # Patch 2: make post-login warm-up probes fault-tolerant
        original_login_flow = self.cl.login_flow

        def safe_login_flow(*args, **kwargs):
            try:
                return original_login_flow(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    f"Post-login flow challenge suppressed (normal after 2FA on flagged IPs). "
                    f"{type(e).__name__}: {e}"
                )
                return True  # Auth succeeded; probe failure is non-fatal

        self.cl.login_flow = safe_login_flow

    def _warmup_session(self):
        """Light probe to confirm the session is alive after fresh login."""
        try:
            self.cl.get_timeline_feed()
            logger.info("Session warm-up OK.")
        except Exception as e:
            logger.warning(f"Warm-up probe skipped (non-fatal): {e}")

    def _apply_jitter(self, base_min=2, base_max=6):
        delay = round(random.uniform(base_min, base_max), 2)
        time.sleep(delay)

    @api_safeguard
    def _login_with_session(self, sessionid):
        """NUCLEAR OPTION: Bypass login entirely by injecting the browser VIP cookie."""
        logger.info("Injecting browser Session ID (bypassing login endpoints)...")
        ensure_dir(self.outdir)
        try:
            self.cl.login_by_sessionid(sessionid)
            # Fetch timeline to verify the cookie is alive
            self.cl.get_timeline_feed()
            logger.info("✓ Session Injection Successful! Authentication bypassed.")
            self.authenticated = True
        except Exception as e:
            logger.error(f"Session injection failed. The sessionid might be expired or invalid. {e}")
            sys.exit(1)

    @api_safeguard
    def _smart_login(self, username, password):
        """Stateful authentication with device pinning and warm-ups."""
        ensure_dir(self.outdir)

        # Attempt to revive an existing session
        if os.path.exists(self.session_file):
            logger.info("Found existing session blueprint.")
            self.cl.load_settings(self.session_file)
            
            # Lightweight check to see if session is still valid
            try:
                self.cl.get_timeline_feed()
                logger.info("✓ Session verified - reusing existing login matrix.")
                self.authenticated = True
                return
            except Exception:
                logger.warning("Session expired or invalid. Clearing stale session and re-authenticating...")
                if os.path.exists(self.session_file):
                    os.remove(self.session_file)

        logger.info(f"Initiating login sequence for @{username}...")

        try:
            self.cl.login(username, password)
            self.cl.dump_settings(self.session_file)
            logger.info("✓ Logged in successfully.")
            self.authenticated = True
            self._warmup_session()

        except TwoFactorRequired:
            if self.totp_secret:
                if pyotp is None:
                    logger.error("pyotp not installed. Run: pip install pyotp")
                    sys.exit(1)
                code = pyotp.TOTP(self.totp_secret).now()
                logger.info(f"Auto-generated 2FA code from authenticator secret.")
            else:
                code = input("Enter 2FA code: ").strip()
            self.cl.login(username, password, verification_code=code)
            self.cl.dump_settings(self.session_file)
            logger.info("✓ 2FA Authentication successful.")
            self.authenticated = True
            self._warmup_session()

    @api_safeguard
    def fetch_single_post(self, url_or_shortcode):
        shortcode = extract_shortcode(url_or_shortcode)
        logger.info(f"Fetching post: {shortcode}")
        self._apply_jitter(1, 3)

        media_pk = self.cl.media_pk_from_code(shortcode)
        media = self.cl.media_info(media_pk)
        logger.info(f"✓ Fetched post by @{media.user.username}")
        return [self._convert_media(media)]

    @api_safeguard
    def _get_user_id_direct(self, username):
        """Raw mobile API call for user ID."""
        res = self.cl.private_request(f"users/{username}/usernameinfo/", params={})
        user_data = res.get("user", {})
        user_id = user_data.get("pk")
        if not user_id:
            raise Exception(f"User @{username} not found")
        return str(user_id), user_data

    @api_safeguard
    def fetch_user_posts(self, username, first_n=0, last_n=0):
        logger.info(f"Fetching posts from @{username}...")
        self._apply_jitter(2, 5)

        user_id, user_data = self._get_user_id_direct(username)
        if user_data.get("is_private", False):
            raise Exception(f"@{username} is a private account.")

        total_posts = user_data.get("media_count", 0)
        fetch_count = total_posts if (first_n == 0 and last_n == 0) else min(first_n + last_n, total_posts)
        
        media_items = []
        max_id = ""

        while len(media_items) < fetch_count:
            count = min(fetch_count - len(media_items), 50)
            res = self.cl.private_request(
                f"feed/user/{user_id}/",
                params={"max_id": max_id, "count": count, "rank_token": self.cl.rank_token, "ranked_content": "true"}
            )

            items = res.get("items", [])
            if not items: break
            media_items.extend(items)
            
            max_id = res.get("next_max_id")
            if not max_id or len(media_items) >= fetch_count: break
            
            # Vital for pagination to prevent bans
            self._apply_jitter(3, 8) 

        logger.info(f"✓ Fetched {len(media_items)} posts.")
        return self._slice_items(media_items, first_n, last_n)

    @api_safeguard
    def fetch_collection(self, collection_name, first_n=0, last_n=0):
        if not self.authenticated:
            raise LoginRequired("Collections require authentication")

        logger.info(f"Fetching collection: '{collection_name}'")
        self._apply_jitter(2, 5)

        # Use private_request directly instead of collection_pk_by_name so that
        # ChallengeRequired isn't swallowed inside instagrapi's collections() internals
        # and can be properly caught by the api_safeguard decorator above.
        res = self.cl.private_request(
            "collections/list/",
            params={
                "collection_types": '["ALL_MEDIA_AUTO_COLLECTION","PRODUCT_AUTO_COLLECTION","MEDIA"]',
                "max_id": "",
            }
        )
        col_pk = None
        for item in res.get("items", []):
            if item.get("collection_name") == collection_name:
                col_pk = item.get("collection_id")
                break
        if not col_pk:
            raise CollectionNotFound(name=collection_name)
        media_items = []
        max_id = None

        while True:
            params = {"include_igtv_preview": "false"}
            if max_id: params["max_id"] = max_id

            res = self.cl.private_request(f"feed/collection/{col_pk}/", params=params)

            for item in res.get("items", []):
                media_dict = item.get("media")
                if media_dict: media_items.append(media_dict)

            max_id = res.get("next_max_id")
            if not max_id: break
            
            self._apply_jitter(3, 8)

        logger.info(f"✓ Fetched {len(media_items)} posts from collection.")
        return self._slice_items(media_items, first_n, last_n)

    def _slice_items(self, media_items, first_n, last_n):
        if last_n > 0 and len(media_items) > (first_n + last_n):
            first_slice = media_items[:first_n] if first_n > 0 else []
            last_slice = media_items[-last_n:] if last_n > 0 else []
            seen_codes = set()
            final_items = []
            for item in first_slice + last_slice:
                code = item.get("code")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    final_items.append(item)
            return final_items
        if first_n > 0: return media_items[:first_n]
        return media_items

    def _convert_media(self, media):
        """Converts internal object to unified dictionary."""
        media_type = 8 if media.media_type == 8 else (2 if media.media_type == 2 else 1)
        product_type = getattr(media, 'product_type', 'feed')

        media_dict = {
            'pk': str(media.pk),
            'id': str(media.id),
            'code': media.code,
            'media_type': media_type,
            'product_type': product_type,
            'caption': {'text': media.caption_text} if media.caption_text else None,
            'user': {'username': media.user.username}
        }

        if media.thumbnail_url: media_dict['image_versions2'] = {'candidates': [{'url': str(media.thumbnail_url)}]}
        if media.video_url: media_dict['video_versions'] = [{'url': str(media.video_url)}]

        if media_type == 8 and hasattr(media, 'resources'):
            media_dict['carousel_media'] = [
                {
                    'pk': str(res.pk),
                    'media_type': 2 if res.media_type == 2 else 1,
                    **({'image_versions2': {'candidates': [{'url': str(res.thumbnail_url)}]}} if res.thumbnail_url else {}),
                    **({'video_versions': [{'url': str(res.video_url)}]} if res.video_url else {})
                }
                for res in media.resources
            ]

        return media_dict