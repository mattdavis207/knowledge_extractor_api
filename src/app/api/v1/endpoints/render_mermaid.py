import httpx
from fastapi import APIRouter, HTTPException

from app.schemas.mermaid import MermaidRequest
from app.services.helpers import encode_mermaid

router = APIRouter()


@router.post("")
async def render_mermaid(req: MermaidRequest):
    print("Received:")
    print(repr(req.mermaid))

    if not req.mermaid.strip():
        raise HTTPException(status_code=400, detail="Mermaid code is empty")

    encoded = encode_mermaid(req.mermaid)

    image_url = f"https://mermaid.ink/img/{encoded}?type=png"

    print("Mermaid URL:", image_url)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(image_url)

    print("Status:", response.status_code)
    print("Response:", response.text[:500])

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=response.text)

    return {"mermaid_image_url": image_url}
