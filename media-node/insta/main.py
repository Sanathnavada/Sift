# import sys
# import argparse
# import logging
# import torch
# import os

# from ..utils import ensure_dir
# from .instagram import InstagramFetcher
# from .service import ScraperService

# # Configure root logger
# logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# logger = logging.getLogger(__name__)

# def main():
#     parser = argparse.ArgumentParser(
#         description='Instagram Scraper - Production Refactored',
#         formatter_class=argparse.RawDescriptionHelpFormatter
#     )

#     mode_group = parser.add_mutually_exclusive_group(required=True)
#     mode_group.add_argument("--public-user", metavar="USERNAME")
#     mode_group.add_argument("--post", metavar="URL")
#     mode_group.add_argument("--private-user", metavar="COLLECTION")

#     parser.add_argument("--username", help="Your Instagram username")
#     parser.add_argument("--password", help="Your Instagram password")

#     parser.add_argument("--first-n", type=int, default=0,
#                        help="Number of most recent posts (0 = all)")
#     parser.add_argument("--last-n", type=int, default=0,
#                        help="Number of oldest posts (0 = none)")

#     parser.add_argument("--outdir", default="./archive")
#     parser.add_argument("--checkpoint", default="checkpoint.json")

#     args = parser.parse_args()

#     # Validate
#     if args.private_user and (not args.username or not args.password):
#         parser.error("--private-user requires --username and --password")

#     # Backward compatibility
#     if args.first_n == 0 and args.last_n == 0:
#         args.first_n = 50

#     ensure_dir(args.outdir)
#     device = "cuda" if torch.cuda.is_available() else "cpu"

#     # Initialize fetcher
#     fetcher = InstagramFetcher(
#         outdir=args.outdir,
#         username=args.username,
#         password=args.password
#     )

#     checkpoint_path = os.path.join(args.outdir, args.checkpoint)

#     # MODE 1: Public User
#     if args.public_user:
#         logger.info(f"=== MODE: Public User (@{args.public_user}) ===")
#         media_items = fetcher.fetch_user_posts(
#             args.public_user,
#             first_n=args.first_n,
#             last_n=args.last_n
#         )
#         ScraperService.process_posts(media_items, args.outdir, device, checkpoint_path)

#     # MODE 2: Single Post
#     elif args.post:
#         logger.info("=== MODE: Single Post ===")
#         try:
#             media_items = fetcher.fetch_single_post(args.post)
#             ScraperService.process_posts(media_items, args.outdir, device)
#         except Exception as e:
#             logger.error(f"Failed: {e}")
#             sys.exit(1)

#     # MODE 3: Collection
#     elif args.private_user:
#         logger.info(f"=== MODE: Collection ('{args.private_user}') ===")
#         media_items = fetcher.fetch_collection(
#             args.private_user,
#             first_n=args.first_n,
#             last_n=args.last_n
#         )
#         ScraperService.process_posts(media_items, args.outdir, device, checkpoint_path)

# if __name__ == "__main__":
#     main()