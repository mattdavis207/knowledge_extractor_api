
from pydantic import BaseModel


class MermaidRequest(BaseModel):
    mermaid: str
