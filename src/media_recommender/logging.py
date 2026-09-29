"""Container-friendly logging configuration without secrets or personal data.

The application never logs credentials, provider tokens, external identities, or
personal viewing history. This module only controls the destination and level of
application logs so container output stays useful and predictable.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_APPLICATION_LOGGER = "media_recommender"


def configure_logging(level: str) -> None:
    """Configure stdout logging suitable for container operation.

    :param level: Validated logging level name.
    """
    logging.basicConfig(level=level, format=_LOG_FORMAT, stream=sys.stdout)
    logging.getLogger(_APPLICATION_LOGGER).setLevel(level)
