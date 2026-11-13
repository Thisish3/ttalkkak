import shlex

from ttalkkak.errors import TtalkkakError
from ttalkkak.export.common import (
    RunBundle,
    dump,
    engine_args,
    export_value,
    gpu_key,
    resource_name,
)


def render(b: RunBundle, image: str | None = None) -> dict[str, str]:
    c = b.candidate
    if c.engine != "vllm":
        raise TtalkkakError(
            "kserve target supports vLLM runs; use --target llmd or k8s", 2
        )
    gpu = {gpu_key(b.vendor): "1"}
    svc = {
        "apiVersion": "serving.kserve.io/v1alpha2",
        "kind": "LLMInferenceService",
        "metadata": {"name": resource_name(b), "labels": {"ttalkkak.run": b.run_id}},
        "spec": {
            "model": {"uri": f"hf://{c.model_id}", "name": c.model_id},
            "replicas": 1,
            "router": {"route": {}, "scheduler": {}},
            "template": {"containers": [{
                "name": "main",
                "image": image or export_value(b, "kserve"),
                "env": [
                    {"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {
                        "name": "hf-token", "key": "HF_TOKEN"}}},
                    {"name": "VLLM_ADDITIONAL_ARGS",
                     "value": shlex.join(engine_args(c))},
                ],
                "resources": {"limits": gpu, "requests": dict(gpu)},
            }]},
        },
    }  # fmt: skip
    return {"llminferenceservice.yaml": dump([svc])}
