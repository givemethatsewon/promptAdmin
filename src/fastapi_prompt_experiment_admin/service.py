from __future__ import annotations

import difflib
import hashlib

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from .models import (
    PromptComponent,
    PromptComponentRevision,
    PromptExperimentRun,
    PromptProfile,
    PromptProfileBinding,
    PromptPromotion,
)
from .schemas import CreateProfileRequest, RecordRunRequest


RUNTIME_SOURCE = "runtime_default"
ARCHIVED_RUNTIME_SOURCE = "runtime_archived"


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PromptRegistry:
    def __init__(self, session: Session, active_slots: list[str]):
        self.session = session
        self.active_slots = list(active_slots)

    def list_profiles(self) -> list[PromptProfile]:
        return (
            self.session.query(PromptProfile)
            .options(selectinload(PromptProfile.bindings).selectinload(PromptProfileBinding.revision))
            .order_by(PromptProfile.updated_at.desc(), PromptProfile.created_at.desc())
            .all()
        )

    def get_profile(self, profile_id: str) -> PromptProfile:
        profile = (
            self.session.query(PromptProfile)
            .options(selectinload(PromptProfile.bindings).selectinload(PromptProfileBinding.revision))
            .filter(PromptProfile.id == profile_id)
            .one_or_none()
        )
        if profile is None:
            raise KeyError(f"Prompt profile not found: {profile_id}")
        return profile

    def resolve_runtime_default(self) -> PromptProfile | None:
        return (
            self.session.query(PromptProfile)
            .filter(PromptProfile.source == RUNTIME_SOURCE, PromptProfile.status == "production")
            .order_by(PromptProfile.updated_at.desc())
            .first()
        )

    def create_profile(self, request: CreateProfileRequest) -> PromptProfile:
        missing = [slot for slot in self.active_slots if slot not in request.slots]
        if missing:
            raise ValueError(f"Missing active prompt slots: {', '.join(missing)}")

        profile = PromptProfile(
            name=request.name.strip(),
            description=request.description,
            source=request.source,
            status=request.status,
            metadata_json=request.metadata,
        )
        self.session.add(profile)
        self.session.flush()

        for slot in self.active_slots:
            revision = self._create_revision(slot, request.slots[slot], source=request.source)
            self.session.add(
                PromptProfileBinding(
                    profile_id=profile.id,
                    slot=slot,
                    component_revision_id=revision.id,
                )
            )

        self.session.commit()
        return self.get_profile(profile.id)

    def record_run(self, request: RecordRunRequest) -> PromptExperimentRun:
        run = PromptExperimentRun(
            run_group=request.run_group,
            variant_key=request.variant_key,
            prompt_profile_id=request.prompt_profile_id,
            status=request.status,
            score=request.score,
            total=request.total,
            passed=request.passed,
            review=request.review,
            error=request.error,
            artifact_uri=request.artifact_uri,
            summary_json=request.summary,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def promote_local(self, profile_id: str, note: str | None = None) -> PromptPromotion:
        profile = self.get_profile(profile_id)
        previous = self.resolve_runtime_default()
        if previous and previous.id != profile.id:
            previous.source = ARCHIVED_RUNTIME_SOURCE
            previous.status = "archived"

        profile.source = RUNTIME_SOURCE
        profile.status = "production"
        slot_hashes = {
            binding.slot: binding.revision.content_hash for binding in self._active_bindings(profile)
        }
        promotion = PromptPromotion(
            source_profile_id=profile.id,
            previous_profile_id=previous.id if previous and previous.id != profile.id else None,
            status="local_applied",
            note=note,
            slot_hashes_json=slot_hashes,
        )
        self.session.add(promotion)
        self.session.commit()
        self.session.refresh(promotion)
        return promotion

    def diff_profile(self, profile_id: str, base_profile_id: str | None = None) -> dict[str, dict]:
        profile = self.get_profile(profile_id)
        base = self.get_profile(base_profile_id) if base_profile_id else self.resolve_runtime_default()
        if base is None:
            return {}

        profile_slots = self.profile_texts(profile)
        base_slots = self.profile_texts(base)
        result: dict[str, dict] = {}
        for slot in self.active_slots:
            before = base_slots.get(slot, "")
            after = profile_slots.get(slot, "")
            if before == after:
                continue
            result[slot] = {
                "before_hash": hash_content(before) if before else None,
                "after_hash": hash_content(after) if after else None,
                "unified": "\n".join(
                    difflib.unified_diff(
                        before.splitlines(),
                        after.splitlines(),
                        fromfile=f"base/{slot}",
                        tofile=f"profile/{slot}",
                        lineterm="",
                    )
                ),
            }
        return result

    def profile_texts(self, profile: PromptProfile) -> dict[str, str]:
        return {binding.slot: binding.revision.content for binding in self._active_bindings(profile)}

    def _active_bindings(self, profile: PromptProfile) -> list[PromptProfileBinding]:
        order = {slot: index for index, slot in enumerate(self.active_slots)}
        return sorted(
            [binding for binding in profile.bindings if binding.slot in order],
            key=lambda binding: order[binding.slot],
        )

    def _create_revision(
        self, component_key: str, content: str, *, source: str
    ) -> PromptComponentRevision:
        component = (
            self.session.query(PromptComponent)
            .filter(PromptComponent.key == component_key)
            .one_or_none()
        )
        if component is None:
            component = PromptComponent(key=component_key, label=component_key, source=source)
            self.session.add(component)
            self.session.flush()

        next_revision = (
            self.session.query(func.max(PromptComponentRevision.revision_number))
            .filter(PromptComponentRevision.component_id == component.id)
            .scalar()
            or 0
        ) + 1
        revision = PromptComponentRevision(
            component_id=component.id,
            revision_number=next_revision,
            content=content,
            content_hash=hash_content(content),
            source=source,
        )
        self.session.add(revision)
        self.session.flush()
        return revision
