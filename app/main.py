from fastapi import FastAPI

from app import __version__
from app.api.routes import health
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__)
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
