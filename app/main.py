import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.errors import register_error_handlers
from app.features.capa_8d.router import page_router as capa_8d_page_router
from app.features.capa_8d.router import router as capa_8d_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(title="KAT AI Service", version="0.1.0")
    register_error_handlers(app)

    # The portal page sends its cookies (credentials: "include"), so origins must be listed, not "*"
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["POST"],
            allow_headers=["*"],
        )

    app.include_router(capa_8d_router)
    app.include_router(capa_8d_page_router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/kat/8dcapa/live")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
