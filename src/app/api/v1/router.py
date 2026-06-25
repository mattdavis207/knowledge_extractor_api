from fastapi import APIRouter

from app.api.v1.endpoints import extract_youtube_transcript

api_router = APIRouter()
api_router.include_router(
    extract_youtube_transcript.router,
    prefix="/extract-youtube-transcript",
    tags=["extract-transcript"],
)
