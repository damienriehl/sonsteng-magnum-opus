#!/usr/bin/env python3
"""Print the redaction-safe identity of a PROD release environment file."""
from __future__ import annotations

import argparse
import pathlib

from prod_release_daemon import CONFIG_DIGEST_KEYS, runtime_config_digest


def read_nonsecret_config(path):
    values = {}
    allowed = set(CONFIG_DIGEST_KEYS)
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in allowed:
            values[key] = value
    return values


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args(argv)
    print(runtime_config_digest(read_nonsecret_config(args.env_file)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
