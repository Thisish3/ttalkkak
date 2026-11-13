from ttalkkak.engines import ADAPTERS
from ttalkkak.errors import TtalkkakError
from ttalkkak.export.common import RunBundle, chip_of, dump, resource_name

_LAUNCHER = ["python3", "-m", "sglang.launch_server"]
_DROP_WITH_VALUE = {"--host", "--port"}
_DROP_FLAG = {"--enable-auto-tool-choice", "--enable-metrics"}
_PARSERS = {
    "--tool-call-parser": "--dyn-tool-call-parser",
    "--reasoning-parser": "--dyn-reasoning-parser",
}


def _worker_args(args: list[str]) -> list[str]:
    if args[:3] == _LAUNCHER:
        args = args[3:]
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in _DROP_WITH_VALUE:
            i += 2
        elif a in _DROP_FLAG:
            i += 1
        elif a in _PARSERS:
            out += [_PARSERS[a], args[i + 1]]
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def render(b: RunBundle, image: str | None = None) -> dict[str, str]:
    c = b.candidate
    if chip_of(b.vendor).vendor.lower() != "nvidia":
        raise TtalkkakError("dynamo target supports NVIDIA runs only", 2)
    if not image:
        raise TtalkkakError(
            "dynamo target needs --image (a Dynamo runtime image for the engine)", 2
        )
    secret = [{"secretRef": {"name": "hf-token-secret"}}]
    graph = {
        "apiVersion": "nvidia.com/v1beta1",
        "kind": "DynamoGraphDeployment",
        "metadata": {"name": resource_name(b), "labels": {"ttalkkak.run": b.run_id}},
        "spec": {"components": [
            {"name": "Frontend", "type": "frontend", "replicas": 1,
             "podTemplate": {"spec": {"containers": [
                 {"name": "main", "image": image, "envFrom": secret}]}}},
            {"name": "worker", "type": "worker", "replicas": 1,
             "podTemplate": {"spec": {"containers": [{
                 "name": "main",
                 "image": image,
                 "command": ["python3", "-m", f"dynamo.{c.engine}"],
                 "args": _worker_args(ADAPTERS[c.engine].build_args(c).args),
                 "envFrom": secret,
                 "resources": {"limits": {"nvidia.com/gpu": "1"}},
             }]}}},
        ]},
    }  # fmt: skip
    return {"dynamo.yaml": dump([graph])}
