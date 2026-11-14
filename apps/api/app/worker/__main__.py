"""Worker entrypoint: python -m app.worker"""

from __future__ import annotations

import logging

from app.core.logging import configure_logging
from app.worker.loop import WorkerRunner, install_signal_handlers


def main() -> None:
    configure_logging()
    runner = WorkerRunner()
    install_signal_handlers(runner)
    runner.run_forever()


if __name__ == "__main__":
    main()
