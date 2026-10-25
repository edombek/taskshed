"""Run the bot with every platform enabled in config."""

import asyncio
import sys

from taskshed.entrypoints.common import build, run


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    asyncio.run(run(build(path)))


if __name__ == "__main__":
    main()
