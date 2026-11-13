from ttalkkak.export.common import (
    RunBundle,
    chip_of,
    dump,
    engine_args,
    export_value,
    gpu_key,
    resource_name,
)
from ttalkkak.platform import Platform

REF = "v1.0.0"
_REPO = "https://github.com/llm-d/llm-d//guides/recipes/modelserver"
def _equals_form(args: list[str]) -> list[str]:
    """--flag value pairs to --flag=value, as the llm-d guides write them."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            out.append(f"{a}={args[i + 1]}")
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def render(b: RunBundle, image: str | None = None) -> dict[str, str]:
    c = b.candidate
    flags = _equals_form(engine_args(c))
    if c.engine == "vllm":
        command, args = ["vllm", "serve"], [c.model_id, *flags]
    else:
        command = ["python3", "-m", "sglang.launch_server"]
        args = [f"--model-path={c.model_id}", "--host=0.0.0.0", "--port=8000", *flags]
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [f"{_REPO}/base/single-host/default?ref={REF}"],
        "namePrefix": f"{resource_name(b)}-",
        "components": [
            f"{_REPO}/components/images/{export_value(b, "llmd." + c.engine)}?ref={REF}"
        ],
        "labels": [{"pairs": {"ttalkkak.run": b.run_id}, "includeSelectors": False}],
        "patches": [{"path": "patch.yaml"}],
    }
    gpu = {gpu_key(b.vendor): "1"}
    patch = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "decode"},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [{
            "name": "modelserver",
            "command": command,
            "args": args,
            "env": [{"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {
                "name": "llm-d-hf-token", "key": "HF_TOKEN"}}}],
            "resources": {"limits": gpu, "requests": dict(gpu)},
            # same overrides as llm-d's optimized-baseline guide: the base probe
            # defaults (~30s) restart the pod before the model finishes loading
            "startupProbe": {"initialDelaySeconds": 15, "periodSeconds": 30,
                             "timeoutSeconds": 5, "failureThreshold": 120},
            "readinessProbe": {"periodSeconds": 5, "timeoutSeconds": 2,
                               "failureThreshold": 3},
        }]}}},
    }  # fmt: skip
    # pin the engine image the run verified (llm-d v1.0.0 components ship older ones)
    patch["spec"]["template"]["spec"]["containers"][0]["image"] = image or Platform(
        chip_of(b.vendor)
    ).image(c.engine)
    return {"kustomization.yaml": dump([kustomization]), "patch.yaml": dump([patch])}
