from pydantic import BaseModel, ConfigDict, Field


class ExternalPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: int = Field(validation_alias="userId")
    id: int
    title: str
    body: str
