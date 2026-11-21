#!/usr/bin/env python3
"""Back up and upgrade drained state using the installed Backup Manager package."""

import argparse
import json
from pathlib import Path
import sys

from backup_manager.config import load_global
from backup_manager.state import StateUnsafe, upgrade_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    config = load_global(args.config)
    if config.errors:
        print("Global configuration validation failed.", file=sys.stderr)
        return 4
    state = config.data["state"]
    try:
        record = upgrade_state(Path(state["path"]), Path(state["anchor_path"]), args.backup.absolute())
    except StateUnsafe:
        print("State upgrade refused. Preserve existing state and backup files for investigation.", file=sys.stderr)
        return 8
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
