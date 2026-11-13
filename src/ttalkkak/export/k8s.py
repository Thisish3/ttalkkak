from ttalkkak.engines import ADAPTERS
from ttalkkak.export.common import RunBundle, chip_of, dump, gpu_key, resource_name
from ttalkkak.platform import Platform


def render(b: RunBundle, image: str | None = None) -> dict[str, str]:
    c = b.candidate
    adapter = ADAPTERS[c.engine]
    port = adapter.container_port
    name = resource_name(b)
    labels = {"app.kubernetes.io/name": name, "ttalkkak.run": b.run_id}
    gpu = {gpu_key(b.vendor): "1"}
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": {
                    "containers": [{
                        "name": "server",
                        "image": image or Platform(chip_of(b.vendor)).image(c.engine),
                        "args": adapter.build_args(c).args,
                        "ports": [{"containerPort": port, "name": "http"}],
                        "env": [{"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {
                            "name": "hf-token", "key": "HF_TOKEN"}}}],
                        "readinessProbe": {"httpGet": {"path": "/health", "port": port},
                                           "periodSeconds": 10, "failureThreshold": 90},
                        "resources": {"limits": gpu, "requests": dict(gpu)},
                        "volumeMounts": [{"name": "shm", "mountPath": "/dev/shm"}],
                    }],
                    "volumes": [{"name": "shm",
                                 "emptyDir": {"medium": "Memory", "sizeLimit": "8Gi"}}],
                },
            },
        },
    }  # fmt: skip
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": dict(labels)},
        "spec": {"selector": {"app.kubernetes.io/name": name},
                 "ports": [{"name": "http", "port": port, "targetPort": port}]},
    }  # fmt: skip
    return {"deployment.yaml": dump([deployment]), "service.yaml": dump([service])}
