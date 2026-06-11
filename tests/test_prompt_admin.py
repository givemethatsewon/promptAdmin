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
    assert "Prompt Admin" in response.text


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
            "run_group": "mobile-header-r1",
            "variant_key": "candidate",
            "prompt_profile_id": profile["id"],
            "score": 100,
            "total": 1,
            "passed": 1,
        },
    )

    assert response.status_code == 201
    runs = client.get("/prompt-admin/runs").json()["runs"]
    assert runs[0]["run_group"] == "mobile-header-r1"
    assert runs[0]["passed"] == 1
