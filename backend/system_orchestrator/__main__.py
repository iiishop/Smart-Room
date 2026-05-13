from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from .orchestrator import SystemOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart Room system orchestrator")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[2] / "config.yaml"),
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logs",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    orchestrator = SystemOrchestrator(args.config)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await orchestrator.start()
    logging.getLogger(__name__).info("System started. Waiting for shutdown signal")

    try:
        await stop_event.wait()
    finally:
        await orchestrator.stop()
        logging.getLogger(__name__).info("System stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
