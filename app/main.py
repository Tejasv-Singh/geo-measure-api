import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.middleware import UploadSizeLimitMiddleware
from app.api.routes import files, health
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.db import create_db_engine, create_session_factory, run_migrations
from app.services.pipeline import fail_interrupted
from app.storage import LocalStorage

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url)
        run_migrations(engine)
        storage = LocalStorage(settings.storage_dir, settings.max_upload_bytes)
        storage.prepare()
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            interrupted = fail_interrupted(session)
        if interrupted:
            logger.warning("Marked %d unfinished file(s) as FAILED after a restart", interrupted)

        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.storage = storage
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(
        UploadSizeLimitMiddleware, path="/api/files/", max_file_bytes=settings.max_upload_bytes
    )
    app.include_router(health.router)
    app.include_router(files.router)
    return app


app = create_app()
