import base64
import zlib

import httpx


async def get_youtube_title(video_id: str) -> str | None:
    url = "https://www.youtube.com/oembed"
    params = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "json",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return response.json().get("title")

def encode_mermaid(mermaid: str) -> str:
     return base64.urlsafe_b64encode(
        mermaid.encode("utf-8")
    ).decode("ascii").rstrip("=")