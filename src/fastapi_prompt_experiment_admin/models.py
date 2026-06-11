from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PromptComponent(Base):
    __tablename__ = "prompt_admin_components"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    source: Mapped[str] = mapped_column(String, default="db")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    revisions: Mapped[list[PromptComponentRevision]] = relationship(
        back_populates="component",
        cascade="all, delete-orphan",
        order_by="PromptComponentRevision.revision_number.asc()",
    )


class PromptComponentRevision(Base):
    __tablename__ = "prompt_admin_component_revisions"
    __table_args__ = (
        UniqueConstraint("component_id", "revision_number", name="uq_prompt_admin_revision_number"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    component_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_admin_components.id", ondelete="CASCADE"), index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    source: Mapped[str] = mapped_column(String, default="db")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    component: Mapped[PromptComponent] = relationship(back_populates="revisions")
    bindings: Mapped[list[PromptProfileBinding]] = relationship(back_populates="revision")


class PromptProfile(Base):
    __tablename__ = "prompt_admin_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft", index=True)
    source: Mapped[str] = mapped_column(String, default="db_experiment", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    bindings: Mapped[list[PromptProfileBinding]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="PromptProfileBinding.slot.asc()",
    )


class PromptProfileBinding(Base):
    __tablename__ = "prompt_admin_profile_bindings"
    __table_args__ = (UniqueConstraint("profile_id", "slot", name="uq_prompt_admin_profile_slot"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_admin_profiles.id", ondelete="CASCADE"), index=True
    )
    slot: Mapped[str] = mapped_column(String, index=True)
    component_revision_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_admin_component_revisions.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[PromptProfile] = relationship(back_populates="bindings")
    revision: Mapped[PromptComponentRevision] = relationship(back_populates="bindings")


class PromptExperimentRun(Base):
    __tablename__ = "prompt_admin_experiment_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_group: Mapped[str] = mapped_column(String, index=True)
    variant_key: Mapped[str] = mapped_column(String, index=True)
    prompt_profile_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_admin_profiles.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default="recorded", index=True)
    score: Mapped[float | None] = mapped_column(nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    review: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[int] = mapped_column(Integer, default=0)
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptPromotion(Base):
    __tablename__ = "prompt_admin_promotions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_profile_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_admin_profiles.id"), index=True
    )
    previous_profile_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_admin_profiles.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default="local_applied", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    slot_hashes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    remote_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
