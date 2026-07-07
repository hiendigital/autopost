"""AutoContentPipeline — CLI entry point.

Commands:
  init-data          Generate a sample data/keywords.xlsx (Data + Cấu Hình sheets).
  run-once           Process the next Pending keyword end-to-end, once (one row per run).

Scheduling is intentionally external: call `run-once` from Windows Task Scheduler / cron
as often as you want; each invocation handles exactly one Pending row.

Global flags:
  --dry-run          Mock all external APIs (no keys, no credits, nothing published).
  --keep-images      Keep generated WebP files in output/ instead of cleaning them up.
"""
from __future__ import annotations

import argparse
import logging
import sys


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _apply_dry_run_flag(args) -> None:
    # Import here so logging is configured first and .env is already loaded.
    from src.env import is_dry_run, set_dry_run

    if getattr(args, "dry_run", False):
        set_dry_run(True)
    if is_dry_run():
        logging.getLogger("main").info("DRY_RUN is ON — external APIs are mocked.")
    else:
        logging.getLogger("main").info("DRY_RUN is OFF — hitting real services.")


def cmd_init_data(args) -> int:
    from src.config_manager import ConfigManager

    path = ConfigManager().create_sample_workbook()
    print(f"Created sample workbook: {path}")
    return 0


def cmd_run_once(args) -> int:
    _apply_dry_run_flag(args)
    from src import pipeline

    processed = pipeline.run_once(keep_images=args.keep_images)
    if not processed:
        print("No Pending keyword to process.")
    return 0


def cmd_retry_facebook(args) -> int:
    _apply_dry_run_flag(args)
    from src import pipeline

    count = pipeline.retry_facebook(include_skipped=args.include_skipped, limit=args.limit)
    if count == 0:
        print("No rows were posted to Facebook.")
    else:
        print(f"Posted {count} row(s) to Facebook.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-post", description="Automated SEO content pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--dry-run", action="store_true", help="Mock all external APIs.")
        p.add_argument("--keep-images", action="store_true", help="Keep temp images in output/.")

    p_init = sub.add_parser("init-data", help="Generate sample keywords.xlsx")
    p_init.set_defaults(func=cmd_init_data)

    p_run = sub.add_parser("run-once", help="Process the next Pending keyword once (one row)")
    add_common(p_run)
    p_run.set_defaults(func=cmd_run_once)

    p_fb = sub.add_parser(
        "retry-facebook",
        help="Re-post Facebook for rows already published on WordPress but missing the FB post",
    )
    p_fb.add_argument("--dry-run", action="store_true", help="Mock all external APIs.")
    p_fb.add_argument(
        "--include-skipped",
        action="store_true",
        help="Also post rows that were published while Facebook was disabled (not just errored ones).",
    )
    p_fb.add_argument(
        "--limit", type=int, default=None, help="Max number of rows to retry this run."
    )
    p_fb.set_defaults(func=cmd_retry_facebook)

    return parser


def main(argv=None) -> int:
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
