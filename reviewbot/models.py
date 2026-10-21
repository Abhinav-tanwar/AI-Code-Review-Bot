from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=400)
    line: int = Field(ge=1)
    severity: Literal["critical", "high", "medium", "low"]
    category: Literal["correctness", "security", "reliability", "performance"]
    title: str = Field(max_length=160)
    explanation: str = Field(max_length=2500)
    recommendation: str = Field(max_length=1500)
    confidence: float = Field(ge=0, le=1)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=3000)
    findings: list[Finding] = Field(max_length=20)
    limitations: list[str] = Field(max_length=12)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diff: str | None = Field(default=None, max_length=100000)
    repository: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pull_number: int | None = Field(default=None, ge=1)
