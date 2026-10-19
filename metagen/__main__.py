"""Usage: python -m metagen CONFIG.json [--out OUTPUT_DIR]"""

import argparse
import sys
from pathlib import Path

from .generator import ConfigError, load_config, write_files


def main() -> int:
    parser = argparse.ArgumentParser(prog="metagen", description="Generate ingestion metadata CSVs from a config.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    for path in write_files(cfg, args.out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
