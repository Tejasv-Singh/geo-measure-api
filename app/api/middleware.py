"""ASGI middleware for request IDs and upload size.

Starlette parses the whole multipart body into a temporary file before the route runs, so the
limit in Storage.save comes too late to protect the disk. UploadSizeLimitMiddleware checks
Content-Length up front and counts bytes for bodies sent without one.
"""

import logging
import time

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import FileTooLargeError, error_body
from app.core.logging import clean_request_id, request_id_var

logger = logging.getLogger("app.requests")

# Room for multipart boundaries, part headers and the source_crs field around the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, path: str, max_file_bytes: int) -> None:
        self.app = app
        self.path = path
        self.max_file_bytes = max_file_bytes
        self.max_body_bytes = max_file_bytes + MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        declared = header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await self.reject(scope, receive, send)
            return

        received = 0
        exceeded = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise FileTooLargeError("The file is larger than the upload limit.")
            return message

        async def guarded_send(message: Message) -> None:
            # Once the limit is hit, drop whatever the app answers and send the 413 below.
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await self.reject(scope, receive, send)

    async def reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = FileTooLargeError(
            "The file is larger than the upload limit.", {"limit_bytes": self.max_file_bytes}
        )
        body = error_body(error.code, error.message, error.details)
        response = JSONResponse(
            body, status_code=error.status_code, headers={"Connection": "close"}
        )
        await response(scope, receive, send)


class RequestIDMiddleware:
    """Tag each request with an ID: the client's X-Request-ID if it is safe, else a new one.

    The ID goes on the response header and on every log line written while handling the request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = clean_request_id(header(scope, b"x-request-id"))
        # Not reset afterwards: each request runs in its own task with a copied context, and
        # Starlette writes the server-error log line after this middleware has returned.
        request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            logger.info("%s %s %s %.0fms", scope["method"], scope["path"], status_code, elapsed)


def header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key == name:
            return str(value.decode("latin-1"))
    return None
