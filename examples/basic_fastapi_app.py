from fastapi import FastAPI
from sqlalchemy import create_engine

from fastapi_prompt_experiment_admin import PromptExperimentAdmin, init_db

engine = create_engine(
    "sqlite:///./prompt_admin_example.db",
    connect_args={"check_same_thread": False},
)
init_db(engine)

app = FastAPI(title="PromptAdmin example")

PromptExperimentAdmin(
    app,
    engine,
    active_slots=["system.v1", "style.v1", "output_contract.v1"],
    mount_path="/prompt-admin",
)


@app.get("/")
def root():
    return {"admin": "/prompt-admin"}
