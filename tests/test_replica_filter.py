from ttalkkak.ui.ops_routes import is_replica

DEP = "ttalkkak-20261117-214304-c1"


def test_vllm_pods_are_replicas():
    assert is_replica(f"{DEP}-7749b7958d-8h6r9", DEP)
    assert is_replica(f"{DEP}-56bb84ccbc-5vmgg", DEP)


def test_endpoint_picker_pod_is_not_a_replica():
    # 데스크탑에서 EPP 파드가 복제본 표와 부하 시험 복제본 수에 끼었다.
    assert not is_replica(f"{DEP}-epp-868dc96df5-snwnj", DEP)


def test_other_pods_and_empty_deployment_are_not_replicas():
    assert not is_replica("ttalkkak-ops-666f7b7f5b-jr5zk", DEP)
    assert not is_replica(DEP, DEP)
    assert not is_replica(f"{DEP}-7749b7958d-8h6r9", "")
