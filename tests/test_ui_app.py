import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from ttalkkak.schemas import GpuInfo, Pick
from ttalkkak.ui.app import UiDeps, create_app

FIX = Path(__file__).parent / "fixtures"
GPU8 = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def make(tmp_path, list_models=None, run_fn=None):
    shutil.copytree(FIX / "skills", tmp_path / "skills")
    (tmp_path / "runs").mkdir()

    def models(**kw):
        return iter(
            [
                SimpleNamespace(
                    id="Qwen/Qwen2.5-1.5B-Instruct",
                    downloads=5,
                    safetensors=SimpleNamespace(parameters={"BF16": 1_543_714_304}),
                )
            ]
        )

    def run(model, skills, run_id):
        d = tmp_path / "runs" / run_id
        d.mkdir()
        (d / "pick.json").write_text(Pick(chosen="c1").model_dump_json())

    deps = UiDeps(
        list_models=list_models or models, detect_gpu=lambda: GPU8, run_fn=run_fn or run
    )
    return TestClient(create_app(tmp_path, deps))


def test_index_page(tmp_path):
    r = make(tmp_path).get("/")
    assert r.status_code == 200 and "/api/models" in r.text and "<html" in r.text


def test_gpu_and_models(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/gpu").json()["name"] == "RTX 3060 Ti"
    rows = c.get("/api/models", params={"q": "qwen"}).json()
    assert rows[0]["id"] == "Qwen/Qwen2.5-1.5B-Instruct" and rows[0]["fits"] is True


def test_models_upstream_failure_is_502(tmp_path):
    def boom(**kw):
        raise OSError("network down")

    r = make(tmp_path, list_models=boom).get("/api/models", params={"q": "x"})
    assert r.status_code == 502 and "network down" in r.json()["error"]


def test_skills(tmp_path):
    rows = make(tmp_path).get("/api/skills").json()
    assert rows[0]["name"] == "tooly" and rows[0]["requires"]["tool_calling"] is True


def test_start_run_then_busy_then_status(tmp_path):
    gate = threading.Event()

    def slow(model, skills, run_id):
        (tmp_path / "runs" / run_id).mkdir()
        gate.wait(5)

    c = make(tmp_path, run_fn=slow)
    r = c.post("/api/runs", json={"model": "m", "skills": ["tooly"]})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert c.post("/api/runs", json={"model": "m", "skills": []}).status_code == 409
    assert c.get(f"/api/runs/{rid}").json()["running"] is True
    gate.set()
    for _ in range(100):
        if not c.get(f"/api/runs/{rid}").json()["running"]:
            break
        time.sleep(0.02)
    assert c.get("/api/runs").json()[0]["run_id"] == rid


def test_bad_request_and_traversal(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/runs", json={"skills": []}).status_code == 400
    assert c.get("/api/runs/..%2F..").status_code == 404
    assert c.get("/api/runs/nope").status_code == 404


def test_export(tmp_path):
    c = make(tmp_path)
    shutil.copytree(FIX / "runs" / "vllm-nvidia", tmp_path / "runs" / "r1")
    r = c.post("/api/runs/r1/export", json={"target": "k8s"})
    assert r.status_code == 200 and "deployment.yaml" in r.json()["files"]
    assert (tmp_path / "runs" / "r1" / "export-k8s" / "deployment.yaml").exists()
    r = c.post("/api/runs/r1/export", json={"target": "dynamo"})
    assert r.status_code == 400 and "--image" in r.json()["error"]
