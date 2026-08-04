"""Point d'entrée du worker RQ.

Existe pour une raison précise : lancé via la CLI `rq worker`, le process ne
configure aucun handler de logging applicatif. Les `logger.info` du pipeline
(progression des étapes, reprises de checkpoint, warnings DataForSEO) partent
alors dans le vide, et `docker compose logs worker` ne montre plus que les
lignes de RQ lui-même — on ne distingue plus un job qui avance d'un job figé.

Lancement :
    cd backend && .venv/bin/python -m workers.rq_worker
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from redis import Redis  # noqa: E402
from rq import Queue, Worker  # noqa: E402

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_NAME = os.getenv("RQ_QUEUE", "pipeline")


def main() -> None:
    connection = Redis.from_url(REDIS_URL)
    queue = Queue(QUEUE_NAME, connection=connection)
    logger.info("Worker RQ démarré — queue '%s' sur %s", QUEUE_NAME, REDIS_URL)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
