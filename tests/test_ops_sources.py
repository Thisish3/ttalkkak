import httpx

from ttalkkak.ops_api.sources import cached, cluster_logs


def test_cached_refreshes_after_ttl():
    clock, calls = [0.0], []
    get = cached(lambda: calls.append(1) or len(calls), 10, now=lambda: clock[0])
    assert get() == 1 and get() == 1
    clock[0] = 11
    assert get() == 2


def test_cluster_logs_reads_envoy_container_of_gateway_pods():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "e1"}}]})
        return httpx.Response(200, text='{"a":1}\n{"b":2}\n')

    http = httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(handler))
    assert cluster_logs(http)() == ['{"a":1}', '{"b":2}']
    assert seen[0][1]["labelSelector"].startswith("gateway.envoyproxy.io/")
    assert seen[1] == (
        "/api/v1/namespaces/envoy-gateway-system/pods/e1/log",
        {"container": "envoy"},
    )
