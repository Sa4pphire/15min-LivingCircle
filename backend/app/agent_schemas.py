"""The browser supplies references, never authoritative facility or route data."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regionId: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    mode: Literal["real", "synthetic"]
    analysisId: str = Field(min_length=1, max_length=100)
    resultRevision: int = Field(ge=0, strict=True)


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)
    conversationId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    context: AgentContext
