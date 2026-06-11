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
    label: str | None = None
    prompt_profile_id: str | None = None
    status: str = "recorded"
    review_state: str = "unreviewed"
    sequence: int = 0
    artifact_uri: str | None = None
    artifact_kind: str | None = None
    metrics: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


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
