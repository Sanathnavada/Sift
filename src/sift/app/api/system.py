"""Small system and compatibility routes kept outside the app factory."""

from fastapi import APIRouter

from .routes import music

router = APIRouter()


@router.get("/callback", tags=["Music"])
async def spotify_root_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    return await music.handle_spotify_auth_callback(
        state=state,
        code=code,
        error=error,
        error_description=error_description,
    )


@router.get("/health")
async def health():
    return {"status": "ok"}
