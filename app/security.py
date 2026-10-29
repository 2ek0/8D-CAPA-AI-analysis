import secrets

from fastapi import Depends, Header

from app.config import Settings, get_settings
from app.errors import AppError


async def require_service_key(
    x_service_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Only the portal may call the service: it sends the shared secret in X-Service-Key."""
    if settings.service_key and not secrets.compare_digest(x_service_key or "", settings.service_key):
        raise AppError(401, "Unauthorised.")
