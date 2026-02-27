import os
import sys
import time
import logging
import requests # Added for graceful exception handling
import json # Added for graceful exception handling
from utils import ensure_dir, extract_shortcode

logger = logging.getLogger(__name__)

try:
    from instagrapi import Client
    from instagrapi.exceptions import (
        LoginRequired, MediaNotFound, TwoFactorRequired, 
        ChallengeRequired, CollectionNotFound
    )
    # Suppress verbose instagrapi logs
    logging.getLogger("instagrapi").setLevel(logging.ERROR)
except ImportError:
    logger.error("Error: instagrapi not installed. Run: pip install instagrapi")
    sys.exit(1)

class InstagramFetcher:
    """
    Production-grade Instagram fetcher.
    COMPLETE RAW API IMPLEMENTATION - Zero GraphQL code paths.
    """

    def __init__(self, outdir, username=None, password=None):
        self.cl = Client()
        self.outdir = outdir
        self.session_file = os.path.join(outdir, "instagram_session.json")
        self.authenticated = False

        # TASK 1: Increased delay range to mimic human behavior and avoid rate limits
        self.cl.delay_range = [2, 6] 

        if username and password:
            self._login(username, password)

    def _verify_session_state(self):
        """Lightweight API call to guarantee the account isn't checkpointed."""
        try:
            self.cl.account_info()
            return True
        except Exception as e:
            logger.debug(f"Session verification failed: {e}")
            return False

    def _login(self, username, password):
        """Smart session management with automatic reuse and fail-fast verification."""
        ensure_dir(self.outdir)

        # 1. Try loading existing session
        if os.path.exists(self.session_file):
            try:
                logger.info("Found existing session file")
                self.cl.load_settings(self.session_file)
                
                if self._verify_session_state():
                    logger.info("✓ Session verified - reusing existing login")
                    self.authenticated = True
                    return
                else:
                    logger.info("Session expired or checkpointed, logging in fresh...")
            except Exception as e:
                logger.warning(f"Could not load session: {e}")

        logger.info(f"Logging in as @{username}...")

        # 2. Fresh Login Flow
        try:
            self.cl.login(username, password)
            self.cl.dump_settings(self.session_file)
            
            # TASK 2: Fail-Fast Verification
            if not self._verify_session_state():
                raise ChallengeRequired("Login succeeded but account is immediately restricted.")

            logger.info("✓ Logged in successfully (session saved & verified)")
            self.authenticated = True

        except TwoFactorRequired:
            code = input("Enter 2FA code: ").strip()
            try:
                # 🛡️ TASK 1: Wrapped the 2FA login call in its own safeguard
                self.cl.login(username, password, verification_code=code)
                self.cl.dump_settings(self.session_file)
                
                if not self._verify_session_state():
                    raise ChallengeRequired("2FA succeeded but account is immediately restricted.")
                    
                logger.info("✓ Logged in with 2FA (session saved)")
                self.authenticated = True
                
            except (ChallengeRequired, requests.exceptions.JSONDecodeError, json.decoder.JSONDecodeError):
                logger.error("\n" + "="*60)
                logger.error("🛑 CHECKPOINT TRIGGERED AFTER 2FA 🛑")
                logger.error("Instagram accepted your 2FA but immediately blocked the session.")
                logger.error("ACTION REQUIRED:")
                logger.error("1. Open the Instagram app on your phone.")
                logger.error("2. Acknowledge the 'Was this you?' prompt.")
                logger.error(f"3. Delete {self.session_file} (if it exists).")
                logger.error("4. Run this script again.")
                logger.error("="*60 + "\n")
                sys.exit(1)
            except Exception as e:
                logger.error(f"2FA Login failed: {e}")
                raise

        except (ChallengeRequired, requests.exceptions.JSONDecodeError, json.decoder.JSONDecodeError):
            # 🛡️ TASK 2: Handle checkpoints gracefully during initial login
            logger.error("\n" + "="*60)
            logger.error("🛑 INSTAGRAM CHECKPOINT TRIGGERED 🛑")
            logger.error("Instagram has flagged this login as suspicious.")
            logger.error("ACTION REQUIRED:")
            logger.error("1. Open the Instagram app on your phone.")
            logger.error("2. Acknowledge the 'Was this you?' prompt.")
            logger.error(f"3. Delete the file: {self.session_file}")
            logger.error("4. Run this script again.")
            logger.error("="*60 + "\n")
            sys.exit(1)

        except Exception as e:
            logger.error(f"Login failed: {e}")
            raise

    # ... [Keep fetch_single_post and _get_user_id_direct exactly as they were] ...
    def fetch_single_post(self, url_or_shortcode):
        shortcode = extract_shortcode(url_or_shortcode)
        logger.info(f"Fetching post: {shortcode}")

        try:
            media_pk = self.cl.media_pk_from_code(shortcode)
            media = self.cl.media_info(media_pk)
            logger.info(f"✓ Fetched post by @{media.user.username}")
            return [self._convert_media(media)]
        except LoginRequired:
            logger.error("This post requires authentication")
            logger.error("Add: --username YOUR_USERNAME --password YOUR_PASSWORD")
            raise
        except MediaNotFound:
            logger.error(f"Post {shortcode} not found or deleted")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch post: {e}")
            raise

    def _get_user_id_direct(self, username):
        try:
            res = self.cl.private_request(f"users/{username}/usernameinfo/", params={})
            user_data = res.get("user", {})
            user_id = user_data.get("pk")
            if not user_id:
                raise Exception(f"User @{username} not found")
            return str(user_id), user_data
        except Exception as e:
            raise Exception(f"Failed to get user ID for @{username}: {e}")

    def fetch_user_posts(self, username, first_n=0, last_n=0):
        # ... [Keep fetch_user_posts exactly as it was, it's structurally sound] ...
        logger.info(f"Fetching posts from @{username}...")
        try:
            user_id, user_data = self._get_user_id_direct(username)
            is_private = user_data.get("is_private", False)
            if is_private:
                logger.error(f"@{username} is private")
                raise Exception("Private account")

            total_posts = user_data.get("media_count", 0)
            logger.info(f"@{username} has {total_posts} posts")

            if first_n == 0 and last_n == 0:
                fetch_count = total_posts
                logger.info("Fetching ALL posts...")
            elif last_n == 0:
                fetch_count = min(first_n, total_posts)
                logger.info(f"Fetching first {fetch_count} posts...")
            else:
                fetch_count = min(first_n + last_n, total_posts)
                logger.info(f"Fetching first {first_n} + last {last_n} posts...")

            media_items = []
            max_id = ""

            while len(media_items) < fetch_count:
                count = min(fetch_count - len(media_items), 50)
                res = self.cl.private_request(
                    f"feed/user/{user_id}/",
                    params={
                        "max_id": max_id,
                        "count": count,
                        "rank_token": self.cl.rank_token,
                        "ranked_content": "true",
                    },
                )

                items = res.get("items", [])
                if not items: break
                media_items.extend(items)
                if len(media_items) >= fetch_count: break

                next_max_id = res.get("next_max_id")
                if not next_max_id: break
                max_id = next_max_id
                time.sleep(1)

            logger.info(f"✓ Fetched {len(media_items)} posts (raw dicts)")
            return self._slice_items(media_items, first_n, last_n)

        except Exception as e:
            logger.error(f"Failed to fetch user posts: {e}")
            raise

    def fetch_collection(self, collection_name, first_n=0, last_n=0):
        """Fetch posts from saved collection with architectural safeguards."""
        if not self.authenticated:
            raise Exception("Collections require authentication")

        logger.info(f"Fetching collection: '{collection_name}'")

        try:
            col_pk = self.cl.collection_pk_by_name(collection_name)
            logger.info(f"Collection ID: {col_pk}")
            
        except CollectionNotFound:
            logger.error(f"Collection '{collection_name}' not found. Check spelling.")
            raise
        except (requests.exceptions.JSONDecodeError, json.decoder.JSONDecodeError):
            # TASK 3: Catch the exact HTML interception error that crashed your script
            logger.error("\n" + "="*60)
            logger.error("🛑 INSTAGRAM API BLOCKED 🛑")
            logger.error("Instagram intercepted the request and returned an HTML challenge page.")
            logger.error("Your IP or account is temporarily restricted.")
            logger.error("ACTION: Open Instagram on your phone to resolve the checkpoint.")
            logger.error("="*60 + "\n")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to initialize collection fetch: {e}")
            raise

        media_items = []
        max_id = None

        while True:
            try:
                params = {"include_igtv_preview": "false"}
                if max_id:
                    params["max_id"] = max_id

                res = self.cl.private_request(f"feed/collection/{col_pk}/", params=params)

                for item in res.get("items", []):
                    media_dict = item.get("media")
                    if media_dict:
                        media_items.append(media_dict)

                next_max_id = res.get("next_max_id")
                if not next_max_id:
                    break

                max_id = next_max_id
                time.sleep(1)

            except Exception as e:
                logger.warning(f"Error fetching collection page: {e}")
                break

        logger.info(f"✓ Fetched {len(media_items)} posts from collection")
        return self._slice_items(media_items, first_n, last_n)

    # ... [Keep _slice_items and _convert_media exactly as they were] ...
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
            logger.info(f"Selected {len(final_items)} posts (first {first_n} + last {last_n})")
            return final_items
        if first_n > 0:
            return media_items[:first_n]
        return media_items

    def _convert_media(self, media):
        if media.media_type == 8: media_type = 8
        elif media.media_type == 2: media_type = 2
        else: media_type = 1

        product_type = media.product_type if hasattr(media, 'product_type') else 'feed'

        media_dict = {
            'pk': str(media.pk),
            'id': str(media.id),
            'code': media.code,
            'media_type': media_type,
            'product_type': product_type,
            'caption': {'text': media.caption_text} if media.caption_text else None,
            'user': {'username': media.user.username}
        }

        if media.thumbnail_url:
            media_dict['image_versions2'] = {'candidates': [{'url': str(media.thumbnail_url)}]}
        if media.video_url:
            media_dict['video_versions'] = [{'url': str(media.video_url)}]

        if media_type == 8 and hasattr(media, 'resources'):
            carousel_media = []
            for resource in media.resources:
                child_dict = {
                    'pk': str(resource.pk),
                    'media_type': 2 if resource.media_type == 2 else 1
                }
                if resource.thumbnail_url:
                    child_dict['image_versions2'] = {'candidates': [{'url': str(resource.thumbnail_url)}]}
                if resource.video_url:
                    child_dict['video_versions'] = [{'url': str(resource.video_url)}]
                carousel_media.append(child_dict)
            media_dict['carousel_media'] = carousel_media

        return media_dict