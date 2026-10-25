"""Run the bot on VK only."""

import asyncio
import sys

from taskshed.entrypoints.common import build, run


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    asyncio.run(run(build(path, only="vk")))


if __name__ == "__main__":
    main()
