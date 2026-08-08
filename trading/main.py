"""Trading signal CLI entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from trading.config import default_trading_config
from trading.signal import generate_trading_signal


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SimpleQuantDemo position signal.")
    parser.add_argument("--db-path", default=None, help="Path to the SQLite database.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = default_trading_config(
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    if args.db_path:
        config = replace(config, db_path=Path(args.db_path))
    config = replace(
        config,
        start_date=args.start_date or config.start_date,
        end_date=args.end_date,
    )
    result = generate_trading_signal(config)
    print(f"Signal date: {result.signal_date:%Y-%m-%d}")
    print(f"Output: {result.output_path}")
    if result.warnings:
        print("[WARNING] 检测到高度相关的因子对:")
        for warning in result.warnings:
            print(f"  - {warning}")


if __name__ == "__main__":
    main()

