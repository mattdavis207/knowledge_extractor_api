from fastapi import APIRouter, HTTPException, status
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    InvalidVideoId,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApiException,
)

router = APIRouter()


@router.get("/{video_id}")
async def get_transcript(video_id: str):
    try:
        ytt_api = YouTubeTranscriptApi()
        fetched_transcript = ytt_api.fetch(video_id, languages=["en"])

        return {
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

    except YouTubeTranscriptApiException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    
