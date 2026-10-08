"""Reject oversized uploads before Starlette spools them to disk.

Starlette parses the whole multipart body into a temporary file before the route runs, so the
limit in Storage.save comes too late to protect the disk. This middleware checks Content-Length
up front and counts bytes for bodies sent without one.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import FileTooLargeError, error_body

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

        declared = content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
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


def content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
