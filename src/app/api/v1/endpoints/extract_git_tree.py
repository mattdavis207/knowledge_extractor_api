from fastapi import APIRouter, HTTPException, status
from app.schemas.git_tree import GitTreeRequest

from app.core.config import settings

router = APIRouter()


@router.get("/")
async def get_git_tree(req: GitTreeRequest):

    if not req.repo_link:
         raise HTTPException(status_code=400, detail="Repo Link not provided")

    

    git_tree_url = f"https://api.github.com/repos/{owner}/REPO/git/trees/TREE_SHA"
