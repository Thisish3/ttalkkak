# 프로젝트 2 데스크탑 실기 검증 요약 (Task 14)

- 기간: 2026-11-17 ~ 2026-11-18, 데스크탑(Windows 10 + WSL2 Ubuntu 22.04 + k3s v1.36.5, RTX 3060 Ti 8GB, RAM 16 GB)
- 브랜치 `project2`(시작 01f4b5a). 이번에 만든 커밋(push 안 함): db1e7e7 (R1), 2b895ab (R8), c434ece (R11), 033175d (R13), 0955a46 (R20), f1bf931 (R21), 0bf874d (R24), ffbe435 (R26)
- 지시문과 다르게 한 결정: `docs/project2-ledger.md` R1~R28

## 2절 spike
- `origin/stage5`에 spike 결과가 없다. 돌리지 않았다(R28). 같은 질문은 4절과 7절 실측으로 답한다.

## 3절 GPU 나눠 쓰기
- 방법 A(time-slicing)가 k3s에서 됐다. device plugin v0.17.0(매니페스트 설치)에 ConfigMap `nvidia-time-slicing`(키 `any`, `replicas: 4`)과 `CONFIG_FILE=/config/any`를 붙였고, allocatable `nvidia.com/gpu`가 1 → 4가 됐다(`gpu-share.log`, R3).

## 4절 한 GPU에 몇 개
- bf16 `Qwen/Qwen2.5-1.5B-Instruct`: **최대 1개**(2개는 KV -0.74 GiB, 3개는 -1.86 GiB). AWQ `Qwen/Qwen2.5-1.5B-Instruct-AWQ`: **최대 2개**(1개 KV 172,176 토큰, 2개는 복제본마다 43,360 토큰, 3개는 -0.42 GiB). 상세: `replicas.md`
- 7~9절은 AWQ, 최대 2개로 했다(R14).

## 5절 콘솔과 맥 연결
- 콘솔 `ops ui --remote`는 WSL의 tailnet 주소 `100.68.102.113:8787`에 떠 있다(systemd `ttalkkak-ui`, R18). 토큰 없이 401, 토큰을 넣으면 `/api/ping` 200.
- 맥은 SSH 터널로 접속한다: `ssh -N -L 18765:100.68.102.113:8787 desk`, 앱 주소 `http://127.0.0.1:18765`(R9). 토큰은 데스크탑 `~/.ttalkkak/console-token`(0600), 사람이 `ttalkkak ops token show`로 읽는다.

## 6절
- 지표 이름: `prom.py`의 8개가 실제 `/metrics`에 모두 있다(`metric-names.md`).
- 429와 고객별 토큰: 콘솔과 Envoy 접근 로그가 같다(429 13건, acme 204 토큰, `gateway-429.md`). 처음엔 Envoy Gateway 설정 누락으로 모든 요청이 500이었고, AI Gateway 값으로 다시 설치했다(R19).

## 7~9절
- 7절: `loadtest-1vs2.md`(최대 사용자 20과 100). 100명 기준 용량 1개 267,009, 2개 272,848 토큰/분(둘 다 열린 부하 확인 통과).
- 8절: `kill-replica.md`. 시험 중 1개를 꺼도 오류 0, 시험 완료, 새 복제본 Ready까지 144초.
- 9절: `autoscale.md`. 1 → 2(용량 사용률 110 %가 2분 넘게) → 1(놀고 있는 복제본 규칙, 늘린 뒤 5분 10초), 새 복제본 준비 107초.

## 10절 맥 앱 화면 확인
- 사람이 맥에서 한다. 결과는 `app-gui-check.md`, 스크린샷은 `app/`. 아직 하지 않았다.

## 기준 표 (스펙 5절)

| 기준 | 결과 | 근거 파일 |
|---|---|---|
| 복제본 여러 개와 라우팅이 데스크탑에서 실제로 돈다 | **통과** (AWQ 2개 Ready, EPP Ready, 게이트웨이 경유 요청이 두 파드에 나뉘어 들어감) | `replicas.md`, `awq-replicas-2.log`, `loadtest-r2-mu100-samples.log`(파드별 실행 요청 수), `cluster-up-awq.log` |
| 콘솔 "실시간" 탭에 실제 지표가 2초마다 갱신된다 | **미확인** (콘솔 API `/api/live`는 실제 Prometheus 값을 준다. 화면의 2초 갱신은 맥 확인 대기) | `autoscale-samples.jsonl`, `gateway-429.md` |
| 콘솔에서 부하 시험을 돌려 복제본 1개와 3개의 결과를 한 그래프에 겹쳐 볼 수 있고, 그 숫자가 결과 문서로 남는다 | **미확인** (3개는 이 GPU에서 뜨지 않아 1개 vs 2개로 함. 숫자는 문서에 있음. 겹친 그래프 화면은 맥 확인 대기) | `loadtest-1vs2.md`, `loadtest-r1*.json`, `loadtest-r2*.json`, `capacity-configmap.json` |
| 복제본 하나를 꺼도 서비스가 이어지는지 실측 결과가 있다 | **통과** | `kill-replica.md`, `kill-replica-podwatch.log`, `kill-replica.json` |
| 부하를 올렸다 내렸을 때 복제본이 자동으로 늘고 주는 실측 기록(시각, 이유, 준비 시간, 그동안의 첫 글자 p99)이 있다 | **통과** (감소는 지시문 규칙이 아닌 "놀고 있는 복제본" 규칙으로 일어남, R27) | `autoscale.md`, `autoscale-ops-log.txt`, `autoscale-samples.jsonl` |
| 데스크탑 앱 설치 파일이 macOS·Windows·Linux로 빌드되고, 맥 앱이 데스크탑의 콘솔 서버에 토큰으로 붙어 실시간 화면이 돈다 | **미확인** (빌드는 통과: 아래 근거. 맥 앱 연결과 실시간 화면은 10절 맥 확인 대기) | `ci-desktop-build.md` |
| NVIDIA·AMD의 박힌 값이 설정 파일로 옮겨졌고 기존 테스트가 모두 통과한다. 견본 칩 파일이 형식 검사를 통과한다 | **통과** (`pytest -q`: 573 passed, 4 skipped. `example-npu.yaml` 포함 3개 모두 `load_chip` 통과) | `pytest.txt`, `chips-check.txt` |
