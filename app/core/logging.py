import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

# The ID is echoed into logs and response headers, so only a safe charset is accepted.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


def configure_logging(level: str) -> None:
    install_request_id_factory()
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT)


def install_request_id_factory() -> None:
    """Stamp every log record with the current request ID, or "-" outside a request."""
    base = logging.getLogRecordFactory()
    if getattr(base, "adds_request_id", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        record.request_id = request_id_var.get() or "-"
        return record

    factory.adds_request_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def clean_request_id(value: str | None) -> str:
    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


@contextmanager
def request_context(request_id: str | None) -> Iterator[str]:
    """Run a block, such as a background job, under a request ID."""
    request_id = clean_request_id(request_id)
    token = request_id_var.set(request_id)
    try:
        yield request_id
    finally:
        request_id_var.reset(token)
