from fastapi import APIRouter

from app.api.v1.endpoints import (
    extract_youtube_transcript,
    render_mermaid,
    extract_git_tree,
    tradingview,
)

api_router = APIRouter()
api_router.include_router(
    extract_youtube_transcript.router,
    prefix="/extract-youtube-transcript",
    tags=["extract-transcript"],
)
api_router.include_router(
    render_mermaid.router,
    prefix="/render-mermaid",
    tags=["render-mermaid"],
)
api_router.include_router(
    extract_git_tree.router,
    prefix="/git-tree",
    tags=["git-tree"]
)
api_router.include_router(
    tradingview.router,
    prefix="/tradingview",
    tags=["tradingview"],
)
