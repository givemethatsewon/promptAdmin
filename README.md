# fastapi-prompt-experiment-admin

> Development preview. The package is usable as a small FastAPI admin today, but
> the public API and database schema are still expected to change.

`fastapi-prompt-experiment-admin` is a small, drop-in FastAPI admin for prompt
experimentation systems. It gives any project the pieces Place2Page needed:

- prompt components and immutable revisions
- named prompt profiles made from slot bindings
- profile diffs
- experiment run records
- `variant_graph.html` lineage view
- per-run generic dashboards
- local runtime default promotion and rollback ledger
- a lightweight HTML admin plus JSON API

## Current Status

Implemented:

- `PromptExperimentAdmin(app, engine, active_slots=[...])` mounting
- prompt profile creation from slot content
- immutable prompt revision storage
- runtime default local promotion ledger
- profile diff JSON API
- profile diff HTML viewer
- experiment run recording
- `variant_graph.html` as the default dashboard
- per-run dashboards for generic artifacts, metrics, and annotations

Not implemented yet:

- Alembic migration package
- auth integration
- remote promotion adapters

It deliberately does **not** assume it can promote to a remote production system.
Remote promotion depends on your project's deployment and database access, so this
package exposes a `PromotionHook` extension point instead.

## Quick Start

```python
from fastapi import FastAPI
from sqlalchemy import create_engine

from fastapi_prompt_experiment_admin import PromptExperimentAdmin, init_db

engine = create_engine("sqlite:///./prompt_admin.db", connect_args={"check_same_thread": False})
init_db(engine)

app = FastAPI()

prompt_admin = PromptExperimentAdmin(
    app,
    engine,
    active_slots=["system.v1", "style.v1", "output_contract.v1"],
    mount_path="/prompt-admin",
)
```

Run the example:

```bash
uv sync --extra dev
uv run uvicorn examples.basic_fastapi_app:app --reload --port 8010
open http://localhost:8010/prompt-admin
```

## Promotion Model

Local promotion changes only this library's registry tables:

1. Archive the previous `runtime_default` profile.
2. Mark the selected candidate profile as `runtime_default`.
3. Write a `prompt_promotions` ledger row with slot hashes and rollback target.

Production promotion is project-specific. If your app has authority to update a
remote runtime, pass a `PromotionHook`:

```python
async def promote_remote(event):
    # call your deploy API, write your production DB, or open a PR
    ...

PromptExperimentAdmin(app, engine, active_slots=[...], promotion_hook=promote_remote)
```

The hook receives a structured event. If it fails, the local transaction has
already recorded the attempted promotion result as inspectable state; your app can
decide whether to retry, rollback, or block.

## API Shape

- `GET /prompt-admin/` - dashboard
- `GET /prompt-admin/variant_graph.html` - lineage graph dashboard
- `GET /prompt-admin/variant_graph` - lineage graph JSON
- `GET /prompt-admin/profiles` - JSON profiles
- `POST /prompt-admin/profiles` - create profile from slot content
- `GET /prompt-admin/profiles/{id}` - profile detail and bound prompt texts
- `GET /prompt-admin/profiles/{id}/diff?base_profile_id=...` - slot diff
- `GET /prompt-admin/profiles/{id}/diff.html?base_profile_id=...` - slot diff UI
- `POST /prompt-admin/promotions` - mark a profile as runtime default
- `GET /prompt-admin/runs` - experiment run list
- `POST /prompt-admin/runs` - record a run summary
- `GET /prompt-admin/runs/{id}/dashboard` - run artifact and review dashboard

Run records intentionally avoid fixed quality-gate fields. Store project-specific
data in:

- `artifact_kind`: for example `html`, `text`, `json`, `image`, `notebook`
- `artifact_uri`: where a human can inspect the result
- `metrics`: arbitrary numeric or textual measurements
- `annotations`: human review notes, labels, decisions, or open questions
- `metadata`: project-specific context

## Why Not Auto-Promote Remote?

Place2Page could update production because the operator had SSH and database
credentials. A reusable library cannot assume that trust boundary. This package
therefore makes local promotion first-class and treats remote rollout as an
explicit adapter.
