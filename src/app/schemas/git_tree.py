
from pydantic import BaseModel


class GitTreeRequest(BaseModel):
    repo_link: str