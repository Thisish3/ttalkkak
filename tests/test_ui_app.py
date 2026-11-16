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


def make(tmp_path, list_models=None, run_fn=None, gpu=GPU8):
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
        list_models=list_models or models, detect_gpu=lambda: gpu, run_fn=run_fn or run
    )
    return TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))


def test_index_page(tmp_path):
    r = make(tmp_path).get("/")
    assert r.status_code == 200 and "app.js" in r.text and "<html" in r.text


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


def test_foreign_host_header_is_rejected(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/gpu", headers={"host": "evil.example.com"}).status_code == 403


def test_post_needs_json_content_type_and_same_origin(tmp_path):
    c = make(tmp_path)
    r = c.post(
        "/api/runs",
        content='{"model":"m","skills":[]}',
        headers={"content-type": "text/plain"},
    )
    assert r.status_code == 415
    r = c.post(
        "/api/runs",
        json={"model": "m", "skills": []},
        headers={"origin": "https://evil.example.com"},
    )
    assert r.status_code == 403
    r = c.post(
        "/api/runs",
        json={"model": "m", "skills": []},
        headers={"origin": "http://testserver"},
    )
    assert r.status_code == 200


def test_bad_inputs_are_400(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/models", params={"q": "x", "limit": "abc"}).status_code == 400
    assert (
        c.post(
            "/api/runs",
            content="not json",
            headers={"content-type": "application/json"},
        ).status_code
        == 400
    )
    assert c.post("/api/runs", json=["a"]).status_code == 400
    assert (
        c.post(
            "/api/runs/r1/export",
            content="nope",
            headers={"content-type": "application/json"},
        ).status_code
        == 400
    )


def test_limit_is_clamped(tmp_path):
    seen = {}

    def models(**kw):
        seen["limit"] = kw["limit"]
        return iter([])

    make(tmp_path, list_models=models).get(
        "/api/models", params={"q": "x", "limit": "-5"}
    )
    assert seen["limit"] == 1


def test_handlers_do_not_block_the_event_loop(tmp_path):
    from ttalkkak.ui import app as appmod

    make(tmp_path)

    import inspect

    for name in ("gpu", "models", "skills", "status", "list_runs"):
        assert name in appmod.SYNC_HANDLERS
    assert all(
        not inspect.iscoroutinefunction(f) for f in appmod.SYNC_HANDLERS.values()
    )


def test_local_run_without_gpu_is_refused_but_nim_is_allowed(tmp_path):
    # no GPU: a local model would pull a multi-GB engine image and then fail
    c = make(tmp_path, gpu=None)
    r = c.post("/api/runs", json={"model": "Qwen/Qwen2.5-0.5B-Instruct", "skills": []})
    assert r.status_code == 400 and "GPU" in r.json()["error"]
    assert (
        c.post("/api/runs", json={"model": "nim:meta/llama", "skills": []}).status_code
        == 200
    )


def test_packaged_web_files_match_desktop_web(tmp_path):
    root = Path(__file__).parent.parent
    c = make(tmp_path)
    for name, kind in (
        ("index.html", "text/html"),
        ("app.css", "text/css"),
        ("app.js", "javascript"),
        ("connect.html", "text/html"),
        ("connect.js", "javascript"),
        ("notify.js", "javascript"),
    ):
        packaged = root / "src/ttalkkak/ui/static" / name
        assert packaged.read_bytes() == (root / "desktop/web" / name).read_bytes()
        r = c.get("/" if name == "index.html" else "/" + name)
        assert r.status_code == 200 and kind in r.headers["content-type"]
        assert r.content == packaged.read_bytes()
