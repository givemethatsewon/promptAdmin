from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fastapi_prompt_experiment_admin import PromptExperimentAdmin, init_db


def make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    app = FastAPI()
    PromptExperimentAdmin(
        app,
        engine,
        active_slots=["system.v1", "style.v1"],
        mount_path="/prompt-admin",
    )
    return TestClient(app)


def create_profile(client: TestClient, name: str, system: str, style: str):
    response = client.post(
        "/prompt-admin/profiles",
        json={
            "name": name,
            "slots": {"system.v1": system, "style.v1": style},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_dashboard_loads():
    client = make_client()
    response = client.get("/prompt-admin/")
    assert response.status_code == 200
    assert "Variant Graph" in response.text


def test_variant_graph_json_links_profiles_runs_and_promotions():
    client = make_client()
    profile = create_profile(client, "baseline", "You are concise.", "Use plain CSS.")
    client.post("/prompt-admin/promotions", json={"profile_id": profile["id"]})
    run_response = client.post(
        "/prompt-admin/runs",
        json={
            "run_group": "copy-r1",
            "variant_key": "baseline",
            "label": "Baseline copy",
            "prompt_profile_id": profile["id"],
            "artifact_kind": "text",
            "artifact_uri": "file://outputs/copy-r1/baseline.txt",
            "metrics": {"word_count": 120},
            "annotations": {"decision": "needs_review"},
        },
    )
    assert run_response.status_code == 201

    graph_response = client.get("/prompt-admin/variant_graph")

    assert graph_response.status_code == 200
    graph = graph_response.json()
    assert graph["runtime_default_id"] == profile["id"]
    assert any(node["kind"] == "profile" for node in graph["nodes"])
    assert any(node["kind"] == "run" for node in graph["nodes"])
    assert any(edge["label"] == "evaluated by" for edge in graph["edges"])
    assert graph["run_groups"][0]["name"] == "copy-r1"
    assert graph["run_groups"][0]["cards"][0]["artifact_kind"] == "text"

    html_response = client.get("/prompt-admin/variant_graph.html")
    assert html_response.status_code == 200
    assert "Runtime Variant Graph" in html_response.text
    assert "Prompt Diff" in html_response.text


def test_create_profile_and_promote_runtime_default():
    client = make_client()
    profile = create_profile(client, "baseline", "You are concise.", "Use plain CSS.")

    response = client.post(
        "/prompt-admin/promotions",
        json={"profile_id": profile["id"], "note": "first baseline"},
    )

    assert response.status_code == 201, response.text
    promotion = response.json()
    assert promotion["source_profile_id"] == profile["id"]
    assert promotion["slot_hashes"]["system.v1"]

    list_response = client.get("/prompt-admin/profiles")
    assert list_response.json()["runtime_default_id"] == profile["id"]


def test_profile_diff_against_runtime_default():
    client = make_client()
    baseline = create_profile(client, "baseline", "You are concise.", "Use plain CSS.")
    client.post("/prompt-admin/promotions", json={"profile_id": baseline["id"]})
    candidate = create_profile(client, "candidate", "You are concise.", "Use polished CSS.")

    response = client.get(f"/prompt-admin/profiles/{candidate['id']}/diff")

    assert response.status_code == 200
    diff = response.json()["diff"]
    assert "style.v1" in diff
    assert "-Use plain CSS." in diff["style.v1"]["unified"]
    assert "+Use polished CSS." in diff["style.v1"]["unified"]
    assert "system.v1" not in diff

    html_response = client.get(f"/prompt-admin/profiles/{candidate['id']}/diff.html")
    assert html_response.status_code == 200
    assert "Prompt Diff" in html_response.text
    assert "Use polished CSS." in html_response.text


def test_remote_promotion_requires_hook():
    client = make_client()
    profile = create_profile(client, "candidate", "A", "B")

    response = client.post(
        "/prompt-admin/promotions",
        json={"profile_id": profile["id"], "apply_remote": True},
    )

    assert response.status_code == 409
    assert "promotion_hook" in response.json()["detail"]
    assert client.get("/prompt-admin/profiles").json()["runtime_default_id"] is None


def test_record_run():
    client = make_client()
    profile = create_profile(client, "candidate", "A", "B")

    response = client.post(
        "/prompt-admin/runs",
        json={
            "run_group": "human-review-r1",
            "variant_key": "candidate",
            "label": "Candidate output",
            "prompt_profile_id": profile["id"],
            "review_state": "needs_review",
            "artifact_kind": "text",
            "artifact_uri": "file://outputs/human-review-r1/candidate.md",
            "metrics": {"tokens": 430},
            "annotations": {"reviewer": "pending"},
        },
    )

    assert response.status_code == 201
    runs = client.get("/prompt-admin/runs").json()["runs"]
    assert runs[0]["run_group"] == "human-review-r1"
    assert runs[0]["review_state"] == "needs_review"
    assert runs[0]["metrics"] == {"tokens": 430}

    dashboard = client.get(f"/prompt-admin/runs/{runs[0]['id']}/dashboard")
    assert dashboard.status_code == 200
    assert "Run Dashboard" in dashboard.text
    assert "Candidate output" in dashboard.text

    group_dashboard = client.get("/prompt-admin/run-groups/human-review-r1/dashboard")
    assert group_dashboard.status_code == 200
    assert "Generic run dashboard" in group_dashboard.text
    assert "tokens" in group_dashboard.text
