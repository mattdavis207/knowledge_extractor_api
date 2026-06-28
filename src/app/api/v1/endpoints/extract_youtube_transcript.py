from fastapi import APIRouter, HTTPException, status
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApiException,
)
from youtube_transcript_api.proxies import (
    GenericProxyConfig,
    ProxyConfig,
    WebshareProxyConfig,
)

from app.core.config import settings
from app.services.helpers import get_youtube_title

router = APIRouter()


def get_youtube_proxy_config() -> ProxyConfig | None:
    webshare_username = settings.youtube_webshare_proxy_username
    webshare_password = settings.youtube_webshare_proxy_password

    if webshare_username or webshare_password:
        if not webshare_username or not webshare_password:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Both YOUTUBE_WEBSHARE_PROXY_USERNAME and "
                    "YOUTUBE_WEBSHARE_PROXY_PASSWORD must be set."
                ),
            )

        locations = None
        if settings.youtube_webshare_proxy_locations:
            locations = [
                location.strip().lower()
                for location in settings.youtube_webshare_proxy_locations.split(",")
                if location.strip()
            ]

        return WebshareProxyConfig(
            proxy_username=webshare_username,
            proxy_password=webshare_password,
            filter_ip_locations=locations,
        )

    if settings.youtube_http_proxy_url or settings.youtube_https_proxy_url:
        return GenericProxyConfig(
            http_url=settings.youtube_http_proxy_url,
            https_url=settings.youtube_https_proxy_url,
        )

    return None


@router.get("/{video_id}")
async def get_transcript(video_id: str):
    try:
        ytt_api = YouTubeTranscriptApi(proxy_config=get_youtube_proxy_config())
        fetched_transcript = ytt_api.fetch(video_id, languages=["en"])

        title = await get_youtube_title(video_id)

        return {
            "video_title": title,
            "video_id": video_id,
            "transcript": fetched_transcript.to_raw_data(),
        }
    except InvalidVideoId as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid YouTube video ID",
        ) from exc

    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No English transcript found for this video",
        ) from exc

    except VideoUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="YouTube video is unavailable",
        ) from exc

    except (RequestBlocked, IpBlocked) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "YouTube blocked the server IP. Configure a rotating residential proxy "
                "for this deployment and try again."
            ),
        ) from exc

    except YouTubeTranscriptApiException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
