from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
        self.templates.env.filters["to_pretty_json"] = to_pretty_json
        self.templates.env.filters["diff_line_class"] = diff_line_class
        self.router = self._build_router()
        app.include_router(self.router, prefix=self.mount_path)

    def _build_router(self) -> APIRouter:
        router = APIRouter(tags=["prompt-admin"])
        get_session = make_session_dependency(self.engine)

        def registry(session: Session = Depends(get_session)) -> PromptRegistry:
            return PromptRegistry(session, self.active_slots)

        @router.get("/", response_class=HTMLResponse)
        @router.get("/variant_graph.html", response_class=HTMLResponse)
        def variant_graph_page(request: Request, service: PromptRegistry = Depends(registry)):
            graph = build_variant_graph(service)
            return self.templates.TemplateResponse(
                request,
                "variant_graph.html",
                {
                    "title": self.title,
                    "mount_path": self.mount_path,
                    "active_slots": self.active_slots,
                    "graph": graph,
                },
            )

        @router.get("/variant_graph")
        def variant_graph_json(service: PromptRegistry = Depends(registry)):
            return build_variant_graph(service)

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

        @router.get("/profiles/{profile_id}/diff.html", response_class=HTMLResponse)
        def diff_profile_page(
            request: Request,
            profile_id: str,
            base_profile_id: str | None = None,
            service: PromptRegistry = Depends(registry),
        ):
            try:
                profile = service.get_profile(profile_id)
                base = service.get_profile(base_profile_id) if base_profile_id else None
                diff = service.diff_profile(profile_id, base_profile_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return self.templates.TemplateResponse(
                request,
                "diff.html",
                {
                    "title": self.title,
                    "mount_path": self.mount_path,
                    "profile": profile,
                    "base_profile": base or service.resolve_runtime_default(),
                    "diff": diff,
                },
            )

        @router.get("/runs")
        def list_runs(session: Session = Depends(get_session)):
            runs = session.query(PromptExperimentRun).order_by(PromptExperimentRun.created_at.desc())
            return {"runs": [serialize_run(run) for run in runs.limit(100).all()]}

        @router.get("/runs/{run_id}")
        def get_run(run_id: str, session: Session = Depends(get_session)):
            run = session.get(PromptExperimentRun, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            return serialize_run(run)

        @router.get("/run-groups/{run_group}/dashboard", response_class=HTMLResponse)
        def run_group_dashboard(
            request: Request,
            run_group: str,
            service: PromptRegistry = Depends(registry),
        ):
            runs = (
                service.session.query(PromptExperimentRun)
                .filter(PromptExperimentRun.run_group == run_group)
                .order_by(PromptExperimentRun.sequence.asc(), PromptExperimentRun.created_at.asc())
                .all()
            )
            if not runs:
                raise HTTPException(status_code=404, detail=f"Run group not found: {run_group}")
            cards = [serialize_run_card(service, run) for run in runs]
            return self.templates.TemplateResponse(
                request,
                "run_group_dashboard.html",
                {
                    "title": self.title,
                    "mount_path": self.mount_path,
                    "run_group": run_group,
                    "cards": cards,
                },
            )

        @router.get("/runs/{run_id}/dashboard", response_class=HTMLResponse)
        def run_dashboard(
            request: Request,
            run_id: str,
            service: PromptRegistry = Depends(registry),
        ):
            run = service.session.get(PromptExperimentRun, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            profile = service.get_profile(run.prompt_profile_id) if run.prompt_profile_id else None
            diff = service.diff_profile(profile.id) if profile is not None else {}
            related_runs = (
                service.session.query(PromptExperimentRun)
                .filter(PromptExperimentRun.run_group == run.run_group)
                .order_by(PromptExperimentRun.sequence.asc(), PromptExperimentRun.created_at.asc())
                .all()
            )
            return self.templates.TemplateResponse(
                request,
                "run_dashboard.html",
                {
                    "title": self.title,
                    "mount_path": self.mount_path,
                    "run": run,
                    "profile": profile,
                    "diff": diff,
                    "related_runs": related_runs,
                },
            )

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
        "label": run.label,
        "prompt_profile_id": run.prompt_profile_id,
        "status": run.status,
        "review_state": run.review_state,
        "sequence": run.sequence,
        "artifact_uri": run.artifact_uri,
        "artifact_kind": run.artifact_kind,
        "metrics": run.metrics_json or {},
        "annotations": run.annotations_json or {},
        "metadata": run.metadata_json or {},
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


def build_variant_graph(service: PromptRegistry) -> dict[str, Any]:
    profiles = service.list_profiles()
    runtime_default = service.resolve_runtime_default()
    runs = (
        service.session.query(PromptExperimentRun)
        .order_by(PromptExperimentRun.run_group.asc(), PromptExperimentRun.sequence.asc())
        .all()
    )
    promotions = (
        service.session.query(PromptPromotion).order_by(PromptPromotion.created_at.asc()).all()
    )

    profile_nodes = [
        {
            "id": f"profile:{profile.id}",
            "kind": "profile",
            "label": profile.name,
            "subtitle": profile.description or profile.source,
            "status": profile.status,
            "href": f"/profiles/{profile.id}/diff.html",
            "metadata": {
                "profile_id": profile.id,
                "source": profile.source,
                "binding_count": len(profile.bindings),
                "runtime_default": bool(runtime_default and runtime_default.id == profile.id),
            },
        }
        for profile in profiles
    ]
    run_nodes = [
        {
            "id": f"run:{run.id}",
            "kind": "run",
            "label": run.label or run.variant_key,
            "subtitle": run.run_group,
            "status": run.review_state,
            "href": f"/runs/{run.id}/dashboard",
            "metadata": {
                "run_id": run.id,
                "variant_key": run.variant_key,
                "artifact_kind": run.artifact_kind,
                "metrics": run.metrics_json or {},
            },
        }
        for run in runs
    ]
    promotion_nodes = [
        {
            "id": f"promotion:{promotion.id}",
            "kind": "promotion",
            "label": promotion.status,
            "subtitle": promotion.note or "runtime promotion",
            "status": promotion.status,
            "href": None,
            "metadata": {
                "promotion_id": promotion.id,
                "source_profile_id": promotion.source_profile_id,
                "previous_profile_id": promotion.previous_profile_id,
            },
        }
        for promotion in promotions
    ]

    edges: list[dict[str, str]] = []
    for run in runs:
        if run.prompt_profile_id:
            edges.append(
                {
                    "from": f"profile:{run.prompt_profile_id}",
                    "to": f"run:{run.id}",
                    "label": "evaluated by",
                }
            )
    for promotion in promotions:
        edges.append(
            {
                "from": f"profile:{promotion.source_profile_id}",
                "to": f"promotion:{promotion.id}",
                "label": "promoted",
            }
        )
        if promotion.previous_profile_id:
            edges.append(
                {
                    "from": f"profile:{promotion.previous_profile_id}",
                    "to": f"promotion:{promotion.id}",
                    "label": "replaced",
                }
            )

    run_groups_by_name: dict[str, list[PromptExperimentRun]] = {}
    for run in runs:
        run_groups_by_name.setdefault(run.run_group, []).append(run)

    run_groups = [
        {
            "name": group,
            "dashboard_href": f"/run-groups/{quote(group)}/dashboard",
            "json_href": "/runs",
            "cards": [serialize_run_card(service, run) for run in group_runs],
        }
        for group, group_runs in sorted(run_groups_by_name.items())
    ]
    linked_profile_ids = {run.prompt_profile_id for run in runs if run.prompt_profile_id}
    unlinked_profiles = [
        serialize_profile_card(service, profile)
        for profile in profiles
        if profile.id not in linked_profile_ids
    ]

    return {
        "nodes": profile_nodes + run_nodes + promotion_nodes,
        "edges": edges,
        "profiles": [serialize_profile(profile) for profile in profiles],
        "runs": [serialize_run(run) for run in runs],
        "promotions": [serialize_promotion(promotion) for promotion in promotions],
        "run_groups": run_groups,
        "unlinked_profiles": unlinked_profiles,
        "runtime_default_id": runtime_default.id if runtime_default else None,
        "runtime_default": serialize_profile(runtime_default) if runtime_default else None,
    }


def serialize_run_card(service: PromptRegistry, run: PromptExperimentRun) -> dict[str, Any]:
    profile = service.get_profile(run.prompt_profile_id) if run.prompt_profile_id else None
    diff = service.diff_profile(profile.id) if profile is not None else {}
    return {
        "id": run.id,
        "dom_id": dom_id(f"{run.run_group}-{run.variant_key}-{run.id}"),
        "label": run.label or run.variant_key,
        "variant_key": run.variant_key,
        "run_group": run.run_group,
        "status": run.status,
        "review_state": run.review_state,
        "artifact_kind": run.artifact_kind or "artifact",
        "artifact_uri": run.artifact_uri,
        "metrics": run.metrics_json or {},
        "annotations": run.annotations_json or {},
        "metadata": run.metadata_json or {},
        "profile": serialize_profile(profile) if profile else None,
        "changed_slots": list(diff.keys()),
        "diff": diff,
        "dashboard_href": f"/runs/{run.id}/dashboard",
    }


def serialize_profile_card(service: PromptRegistry, profile: PromptProfile) -> dict[str, Any]:
    diff = service.diff_profile(profile.id)
    return {
        "id": profile.id,
        "dom_id": dom_id(f"profile-{profile.id}"),
        "label": profile.name,
        "status": profile.status,
        "source": profile.source,
        "description": profile.description,
        "profile": serialize_profile(profile),
        "changed_slots": list(diff.keys()),
        "diff": diff,
        "dashboard_href": f"/profiles/{profile.id}/diff.html",
    }


def dom_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return normalized or "node"


def diff_line_class(line: str) -> str:
    if line.startswith("@@"):
        return "line-hunk"
    if line.startswith("+++") or line.startswith("---"):
        return "line-file"
    if line.startswith("+"):
        return "line-added"
    if line.startswith("-"):
        return "line-removed"
    return "line-context"


def to_pretty_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True)
