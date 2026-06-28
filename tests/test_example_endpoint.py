from fastapi.testclient import TestClient

from app.api.v1.endpoints import extract_youtube_transcript
from app.main import create_app


class StubFetchedTranscript:
    def to_raw_data(self) -> list[dict[str, float | str]]:
        return [{"text": "Hello world", "start": 0.0, "duration": 1.5}]


class StubYouTubeTranscriptApi:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def fetch(self, video_id: str, languages: list[str]) -> StubFetchedTranscript:
        assert video_id == "abc123"
        assert languages == ["en"]
        return StubFetchedTranscript()


async def stub_get_youtube_title(video_id: str) -> str:
    assert video_id == "abc123"
    return "Stub video"


def test_extract_youtube_transcript_path(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_youtube_transcript,
        "YouTubeTranscriptApi",
        StubYouTubeTranscriptApi,
    )
    monkeypatch.setattr(extract_youtube_transcript, "get_youtube_title", stub_get_youtube_title)
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/extract-youtube-transcript/abc123")

    assert response.status_code == 200
    assert response.json() == {
        "video_title": "Stub video",
        "video_id": "abc123",
        "transcript": [{"text": "Hello world", "start": 0.0, "duration": 1.5}],
    }
