#!/usr/bin/env python3
"""Manual end-to-end test: Drive API -> LLM -> Discord."""

import argparse
import os
import sys

import gdrive_sync
import marketing_pipeline


def main():
    parser = argparse.ArgumentParser(description="Run one marketing pipeline test")
    parser.add_argument(
        "--local-path",
        help="Override GDRIVE_LOCAL_PATH for a local test without touching the Railway volume",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="Override MARKETING_MAX_TOKENS for a lower-cost manual test",
    )
    args = parser.parse_args()
    if args.local_path:
        os.environ["GDRIVE_LOCAL_PATH"] = os.path.abspath(args.local_path)
    if args.max_tokens:
        os.environ["MARKETING_MAX_TOKENS"] = str(args.max_tokens)

    marketing_pipeline.load_env_files()
    marketing_pipeline.log("Test", "Drive API sync started")
    count = gdrive_sync.run_sync()
    marketing_pipeline.log("Test", f"Drive API sync passed with {count} markdown file(s)")
    marketing_pipeline.log("Test", "end-to-end marketing job started")
    marketing_pipeline.run_marketing_job()
    marketing_pipeline.log("Test", "end-to-end marketing job passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        marketing_pipeline.log("TEST ERROR", str(exc))
        sys.exit(1)
