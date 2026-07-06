import httpx
from fastapi import APIRouter, HTTPException, status
from app.schemas.git_tree import GitTreeRequest

from app.core.config import settings


from urllib.parse import urlparse
from pathlib import PurePosixPath

router = APIRouter()


@router.get("/")
async def get_git_tree(req: GitTreeRequest):

     if not req.repo_link:
          raise HTTPException(status_code=400, detail="Repo Link not provided")

     repo_link = req.repo_link

     path_string = urlparse(repo_link).path
     path = PurePosixPath(path_string)

     parts = path.parts  

     # Extract important parts 
     owner = parts[1]
     repo = parts[2]
     tree_sha = parts[-1]

     query_params = {"recursive": True}

     git_tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}"

     async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(git_tree_url, params=query_params)

     if response.status_code != 200:
        raise HTTPException(status_code=500, detail=response.text)
     
     return response.json()