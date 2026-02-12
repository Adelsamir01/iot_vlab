#!/usr/bin/env python3
"""scan_library.py — Scan the firmware library and return available configs."""

import json
from pathlib import Path

LIBRARY_DIR = Path(__file__).resolve().parent / "library"


def scan() -> list[dict]:
    """Return a list of firmware configs found in the library."""
    configs = []
    if not LIBRARY_DIR.is_dir():
        return configs
    for cfg_file in sorted(LIBRARY_DIR.rglob("config.json")):
        try:
            data = json.loads(cfg_file.read_text())
            data["_dir"] = str(cfg_file.parent)
            configs.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] Skipping {cfg_file}: {exc}")
    return configs


if __name__ == "__main__":
    for fw in scan():
        print(json.dumps(fw, indent=2))
