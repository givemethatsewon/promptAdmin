from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .database import make_session_dependency
from .models import PromptExperimentRun, PromptProfile, PromptPromotion
from .schemas import (
    CreateProfileRequest,
    PromoteRequest,
    PromotionEvent,
    PromotionHook,
    RecordRunRequest,
)
from .service import PromptRegistry


class PromptExperimentAdmin:
    def __init__(
        self,
        app: FastAPI,
        engine: Engine,
        *,
        active_slots: list[str],
        mount_path: str = "/prompt-admin",
        title: str = "Prompt Admin",
        promotion_hook: PromotionHook | Callable[[PromotionEvent], Awaitable[dict[str, Any] | None]]
        | None = None,
    ) -> None:
        self.engine = engine
        self.active_slots = active_slots
        self.mount_path = mount_path.rstrip("/") or "/prompt-admin"
        self.title = title
        self.promotion_hook = promotion_hook
        self.templates = Jinja2Templates(
            directory=str(Path(__file__).resolve().parent / "templates")
        )
        self.router = self._build_router()
        app.include_router(self.router, prefix=self.mount_path)

    def _build_router(self) -> APIRouter:
        router = APIRouter(tags=["prompt-admin"])
        get_session = make_session_dependency(self.engine)

        def registry(session: Session = Depends(get_session)) -> PromptRegistry:
            return PromptRegistry(session, self.active_slots)

        @router.get("/", response_class=HTMLResponse)
        def dashboard(request: Request, service: PromptRegistry = Depends(registry)):
            profiles = service.list_profiles()
            runtime_default = service.resolve_runtime_default()
            runs = (
                service.session.query(PromptExperimentRun)
                .order_by(PromptExperimentRun.created_at.desc())
                .limit(20)
                .all()
            )
            promotions = (
                service.session.query(PromptPromotion)
                .order_by(PromptPromotion.created_at.desc())
                .limit(20)
                .all()
            )
            return self.templates.TemplateResponse(
                request,
                "dashboard.html",
                {
                    "title": self.title,
                    "mount_path": self.mount_path,
                    "active_slots": self.active_slots,
                    "profiles": profiles,
                    "runtime_default": runtime_default,
                    "runs": runs,
                    "promotions": promotions,
                },
            )

        @router.get("/profiles")
        def list_profiles(service: PromptRegistry = Depends(registry)):
            runtime_default = service.resolve_runtime_default()
            return {
                "profiles": [serialize_profile(profile) for profile in service.list_profiles()],
                "runtime_default_id": runtime_default.id if runtime_default is not None else None,
            }

        @router.post("/profiles", status_code=201)
        def create_profile(
            payload: CreateProfileRequest, service: PromptRegistry = Depends(registry)
        ):
            try:
                return serialize_profile(service.create_profile(payload), include_slots=True)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        @router.get("/profiles/{profile_id}")
        def get_profile(profile_id: str, service: PromptRegistry = Depends(registry)):
            try:
                return serialize_profile(service.get_profile(profile_id), include_slots=True)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @router.get("/profiles/{profile_id}/diff")
        def diff_profile(
            profile_id: str,
            base_profile_id: str | None = None,
            service: PromptRegistry = Depends(registry),
        ):
            try:
                return {"diff": service.diff_profile(profile_id, base_profile_id)}
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @router.get("/runs")
        def list_runs(session: Session = Depends(get_session)):
            runs = session.query(PromptExperimentRun).order_by(PromptExperimentRun.created_at.desc())
            return {"runs": [serialize_run(run) for run in runs.limit(100).all()]}

        @router.post("/runs", status_code=201)
        def record_run(payload: RecordRunRequest, service: PromptRegistry = Depends(registry)):
            return serialize_run(service.record_run(payload))

        @router.post("/promotions", status_code=201)
        async def promote(payload: PromoteRequest, service: PromptRegistry = Depends(registry)):
            if payload.apply_remote and self.promotion_hook is None:
                raise HTTPException(
                    status_code=409,
                    detail="Remote promotion requested but no promotion_hook is configured.",
                )

            try:
                promotion = service.promote_local(payload.profile_id, note=payload.note)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            if payload.apply_remote:
                event = PromotionEvent(
                    promotion_id=promotion.id,
                    profile_id=promotion.source_profile_id,
                    previous_profile_id=promotion.previous_profile_id,
                    slot_hashes=promotion.slot_hashes_json,
                    note=promotion.note,
                )
                remote_result = await self.promotion_hook(event)
                promotion.remote_result_json = remote_result or {}
                promotion.status = "remote_applied"
                service.session.commit()
                service.session.refresh(promotion)

            return serialize_promotion(promotion)

        return router


def serialize_profile(profile: PromptProfile, *, include_slots: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "status": profile.status,
        "source": profile.source,
        "metadata": profile.metadata_json or {},
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
        "binding_count": len(profile.bindings),
    }
    if include_slots:
        payload["slots"] = {
            binding.slot: {
                "revision_id": binding.component_revision_id,
                "revision_number": binding.revision.revision_number,
                "content_hash": binding.revision.content_hash,
                "content": binding.revision.content,
            }
            for binding in sorted(profile.bindings, key=lambda item: item.slot)
        }
    return payload


def serialize_run(run: PromptExperimentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_group": run.run_group,
        "variant_key": run.variant_key,
        "prompt_profile_id": run.prompt_profile_id,
        "status": run.status,
        "score": run.score,
        "total": run.total,
        "passed": run.passed,
        "review": run.review,
        "error": run.error,
        "artifact_uri": run.artifact_uri,
        "summary": run.summary_json or {},
        "created_at": run.created_at.isoformat(),
    }


def serialize_promotion(promotion: PromptPromotion) -> dict[str, Any]:
    return {
        "id": promotion.id,
        "source_profile_id": promotion.source_profile_id,
        "previous_profile_id": promotion.previous_profile_id,
        "status": promotion.status,
        "note": promotion.note,
        "slot_hashes": promotion.slot_hashes_json,
        "remote_result": promotion.remote_result_json,
        "created_at": promotion.created_at.isoformat(),
    }
