from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    source: str = "db_experiment"
    status: str = "experiment"
    slots: dict[str, str]
    metadata: dict[str, Any] | None = None


class RecordRunRequest(BaseModel):
    run_group: str
    variant_key: str
    prompt_profile_id: str | None = None
    status: str = "recorded"
    score: float | None = None
    total: int = 0
    passed: int = 0
    review: int = 0
    error: int = 0
    artifact_uri: str | None = None
    summary: dict[str, Any] | None = None


class PromoteRequest(BaseModel):
    profile_id: str
    note: str | None = None
    apply_remote: bool = False


class PromotionEvent(BaseModel):
    promotion_id: str
    profile_id: str
    previous_profile_id: str | None
    slot_hashes: dict[str, str]
    note: str | None = None


class PromotionHook(Protocol):
    async def __call__(self, event: PromotionEvent) -> dict[str, Any] | None: ...
