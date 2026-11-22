"""Preview a parser against one copied Discord post without contacting Discord."""

import argparse
from pathlib import Path

from core import config, parse

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("file", type=Path, help="One message or flattened embed in UTF-8")
    cli.add_argument("--config", default="config.toml")
    cli.add_argument("--channel", type=int, required=True)
    args = cli.parse_args()
    matches = parse(
        config(args.config), args.file.read_text(encoding="utf-8"), args.channel
    )
    for kind, player, server in sorted(matches):
        print(f"{kind} | {player} | {server}")
    if not matches:
        cli.exit(1, "No match. Check patterns and channel mapping.\n")
