from pathlib import Path

import pytest
import yaml

from ttalkkak.errors import TtalkkakError
from ttalkkak.export import dynamo, k8s, kserve, llmd
from ttalkkak.export.common import load_run

RUNS = Path(__file__).parent / "fixtures/runs"
VN, SN, VA = (load_run(RUNS / r) for r in ("vllm-nvidia", "sglang-nvidia", "vllm-amd"))
MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


def docs(files, name):
    return list(yaml.safe_load_all(files[name]))


def container(d):
    return d["spec"]["template"]["spec"]["containers"][0]


def test_k8s_vllm_deployment_and_service():
    files = k8s.render(VN)
    dep, svc = docs(files, "deployment.yaml")[0], docs(files, "service.yaml")[0]
    assert dep["kind"] == "Deployment" and svc["kind"] == "Service"
    c = container(dep)
    assert c["image"] == "vllm/vllm-openai:v0.33.0"
    assert c["args"][:2] == ["--model", MODEL] and "--kv-cache-dtype" in c["args"]
    assert c["ports"][0]["containerPort"] == 8000
    assert c["readinessProbe"]["httpGet"]["path"] == "/health"
    assert c["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert c["env"][0]["valueFrom"]["secretKeyRef"]["name"] == "hf-token"
    assert svc["spec"]["ports"][0]["port"] == 8000


def test_k8s_sglang_uses_its_port_and_launcher():
    c = container(docs(k8s.render(SN), "deployment.yaml")[0])
    assert c["image"] == "lmsysorg/sglang:v0.5.23"
    assert c["args"][:3] == ["python3", "-m", "sglang.launch_server"]
    assert c["ports"][0]["containerPort"] == 30000


def test_kserve_vllm():
    d = docs(kserve.render(VN), "llminferenceservice.yaml")[0]
    assert d["apiVersion"] == "serving.kserve.io/v1alpha2"
    assert d["kind"] == "LLMInferenceService"
    assert d["spec"]["model"]["uri"] == f"hf://{MODEL}"
    c = d["spec"]["template"]["containers"][0]
    extra = next(e["value"] for e in c["env"] if e["name"] == "VLLM_ADDITIONAL_ARGS")
    assert "--kv-cache-dtype" in extra and "--tool-call-parser" in extra
    assert "--model" not in extra and "--port" not in extra
    assert c["image"] == "ghcr.io/llm-d/llm-d-cuda:v0.9.0"


def test_kserve_amd_image_and_gpu_key():
    c = docs(kserve.render(VA), "llminferenceservice.yaml")[0]["spec"]["template"][
        "containers"
    ][0]
    assert c["image"] == "ghcr.io/llm-d/llm-d-rocm:v0.9.0"
    assert c["resources"]["limits"]["amd.com/gpu"] == "1"


def test_kserve_rejects_sglang():
    with pytest.raises(TtalkkakError, match="vLLM"):
        kserve.render(SN)


def test_llmd_vllm_overlay():
    files = llmd.render(VN)
    k = docs(files, "kustomization.yaml")[0]
    assert all("?ref=v1.0.0" in r for r in k["resources"] + k["components"])
    assert k["resources"][0].endswith(
        "guides/recipes/modelserver/base/single-host/default?ref=v1.0.0"
    )
    assert "images/gpu-vllm/release" in k["components"][0]
    p = docs(files, "patch.yaml")[0]
    assert p["kind"] == "Deployment" and p["metadata"]["name"] == "decode"
    c = container(p)
    assert c["name"] == "modelserver" and c["command"] == ["vllm", "serve"]
    assert c["args"][0] == MODEL and "--model" not in c["args"]


def test_llmd_sglang_on_amd_component_and_port():
    sa = SN.model_copy(update={"vendor": "amd"})
    files = llmd.render(sa)
    k = docs(files, "kustomization.yaml")[0]
    assert "images/amd-sglang/rocm720-mi30x" in k["components"][0]
    c = container(docs(files, "patch.yaml")[0])
    assert c["command"] == ["python3", "-m", "sglang.launch_server"]
    assert "--port=8000" in c["args"] and f"--model-path={MODEL}" in c["args"]


def test_dynamo_vllm_graph():
    d = docs(dynamo.render(VN, image="nvcr.io/x/vllm-runtime:t"), "dynamo.yaml")[0]
    assert d["apiVersion"] == "nvidia.com/v1beta1"
    assert d["kind"] == "DynamoGraphDeployment"
    comps = {c["type"]: c for c in d["spec"]["components"]}
    w = comps["worker"]["podTemplate"]["spec"]["containers"][0]
    assert w["command"] == ["python3", "-m", "dynamo.vllm"]
    assert (
        "--tool-call-parser" not in w["args"]
        and "--enable-auto-tool-choice" not in w["args"]
    )
    assert w["args"][w["args"].index("--dyn-tool-call-parser") + 1] == "hermes"
    assert w["args"][:2] == ["--model", MODEL]


def test_dynamo_requires_image_and_nvidia():
    with pytest.raises(TtalkkakError, match="--image"):
        dynamo.render(VN)
    with pytest.raises(TtalkkakError, match="NVIDIA"):
        dynamo.render(VA, image="x")


@pytest.mark.parametrize("mod", [k8s, kserve, llmd])
def test_no_token_values_in_output(mod, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_SECRET_VALUE_123")
    for text in mod.render(VN).values():
        assert "hf_SECRET_VALUE_123" not in text


@pytest.mark.parametrize("mod,b", [(k8s, VN), (kserve, VN), (llmd, SN)])
def test_no_yaml_anchors(mod, b):
    for text in mod.render(b).values():
        assert "&id" not in text and "*id" not in text


def test_llmd_patch_overrides_startup_probe_like_upstream():
    c = container(docs(llmd.render(VN), "patch.yaml")[0])
    assert c["startupProbe"] == {
        "initialDelaySeconds": 15,
        "periodSeconds": 30,
        "timeoutSeconds": 5,
        "failureThreshold": 120,
    }
    assert c["readinessProbe"]["failureThreshold"] == 3


def test_llmd_pins_the_engine_image_the_run_verified():
    assert container(docs(llmd.render(VN), "patch.yaml")[0])["image"] == (
        "vllm/vllm-openai:v0.33.0"
    )
    sa = SN.model_copy(update={"vendor": "amd"})
    assert container(docs(llmd.render(sa), "patch.yaml")[0])["image"] == (
        "lmsysorg/sglang:v0.5.23-rocm720-mi30x"
    )


@pytest.mark.parametrize(
    "mod,b,name_of",
    [
        (
            kserve,
            VN,
            lambda f: docs(f, "llminferenceservice.yaml")[0]["metadata"]["name"],
        ),
        (llmd, VN, lambda f: docs(f, "kustomization.yaml")[0]["namePrefix"]),
        (k8s, VN, lambda f: docs(f, "deployment.yaml")[0]["metadata"]["name"]),
    ],
)
def test_resource_names_include_run_id(mod, b, name_of):
    other = b.model_copy(update={"run_id": "20261106-120000"})
    a, z = name_of(mod.render(b)), name_of(mod.render(other))
    assert a != z and "20261106-120000" in z


def test_dynamo_name_includes_run_id():
    other = VN.model_copy(update={"run_id": "20261106-120000"})
    d = docs(dynamo.render(other, image="x"), "dynamo.yaml")[0]
    assert "20261106-120000" in d["metadata"]["name"]


def test_names_are_valid_dns_labels():
    weird = VN.model_copy(update={"run_id": "My_Run.2026"})
    n = docs(k8s.render(weird), "deployment.yaml")[0]["metadata"]["name"]
    import re

    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", n) and len(n) <= 63


def test_export_llmd_component_from_chip():
    from ttalkkak.chips import chip_by_name

    for b, chip in ((VN, "nvidia-gpu"), (VA, "amd-gpu")):
        k = docs(llmd.render(b), "kustomization.yaml")[0]
        want = chip_by_name(chip).export[f"llmd.{b.candidate.engine}"]
        assert k["components"][0].endswith(f"/components/images/{want}?ref={llmd.REF}")


def test_export_kserve_image_from_chip():
    from ttalkkak.chips import chip_by_name

    c = docs(kserve.render(VA), "llminferenceservice.yaml")[0]["spec"]["template"][
        "containers"
    ][0]
    assert c["image"] == chip_by_name("amd-gpu").export["kserve"]


def test_export_chip_without_export_entry_is_clear_error():
    b = VN.model_copy(update={"vendor": "example-npu"})
    with pytest.raises(TtalkkakError, match="example-npu"):
        kserve.render(b)


def test_gpu_key_accepts_chip_name():
    from ttalkkak.export.common import gpu_key

    assert gpu_key("amd-gpu") == "amd.com/gpu"
    assert gpu_key("nvidia-gpu") == "nvidia.com/gpu"


def test_dynamo_rejects_non_nvidia_chip():
    with pytest.raises(TtalkkakError, match="NVIDIA"):
        dynamo.render(VA, image="x")


def test_applied_check_unsupported_engine():
    from ttalkkak.schemas import AppliedConfig
    from ttalkkak.verify.applied import check_applied

    r = check_applied("trtllm", VN.candidate, AppliedConfig())
    assert r == {
        "status": "unsupported",
        "reason": "이 엔진은 설정 적용 확인을 지원하지 않습니다",
    }
    assert check_applied("vllm", VN.candidate, AppliedConfig())["status"] in (
        "ok",
        "mismatch",
    )
