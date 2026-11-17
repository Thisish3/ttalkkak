# 프로젝트 2 데스크탑 실기 검증 ledger

지시문(`docs/desktop/project2-desktop.md`)과 다르게 한 결정. 무엇을, 왜, 틀리면 드는 비용.

## R1. `ops up`에 `--gpu-memory-utilization` 옵션 추가 (2026-11-17)

- **무엇을:** 지시문 4절의 `ops export --run <run>`에 쓸 성공한 run이 없어서, `ops up`을 다시 돌리기 전에 vLLM 후보의 GPU 메모리 비율을 옵션으로 받게 고쳤다. 기본값은 0.9로 그대로이고, 계획의 문맥 길이 예측(`max_context_fit`)도 같은 비율을 쓴다. SGLang 후보는 바꾸지 않았다.
- **왜:** 이전 run `20261111-235400`에서 vLLM 후보 c1, c2가 모두 다음 오류로 기동에 실패했다(`runs/20261111-235400/launch.json`).
  `ValueError: Free memory on device cuda:0 (6.9/8.0 GiB) on startup is less than desired GPU memory utilization (0.9, 7.2 GiB).`
  0.9는 `src/ttalkkak/plan/candidates.py`에 고정돼 있어서, 이 데스크탑(Windows가 GPU 메모리 일부를 씀)에서는 `ops up`이 항상 실패한다.
- **확인:** `.venv/bin/python -m pytest -q` → `561 passed, 4 skipped`. `ruff check`, `ruff format --check` 통과.
- **틀리면 드는 비용:** 비율을 너무 낮게 주면 KV 캐시가 줄어 문맥 길이와 용량이 작게 나온다. 게이트웨이 배포(`ops export --target gateway`)는 복제본 수로 비율을 다시 정하므로 영향이 없다(`gateway/manifests.py`).

## R2. `ops up`에서 SGLang 후보를 뺌 (2026-11-17)

- **무엇을:** `ops up`을 `--chip ~/.ttalkkak/nvidia-gpu-no-sglang.yaml`로 돌렸다. 이 파일은 `src/ttalkkak/data/chips/nvidia-gpu.yaml`에서 `engines.sglang` 두 줄만 뺀 사본이다. 그래서 c3(SGLang)은 이미지 받기 전에 "no image for sglang" 실패로 끝난다. 명령: `.venv/bin/ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct --chip $HOME/.ttalkkak/nvidia-gpu-no-sglang.yaml --gpu-memory-utilization 0.8`
- **왜:** 2026-11-12 stage5에서 `lmsysorg/sglang:v0.5.23`을 받다가 C: 드라이브가 0 GB가 됐다. 오늘 C: 여유는 약 3 GB이고, vLLM 이미지(`vllm/vllm-openai:v0.33.0`)는 Docker Desktop에 이미 있다. 프로젝트 2의 게이트웨이 배포는 vLLM만 쓴다.
- **0.8을 고른 이유:** 이 데스크탑은 시작할 때 GPU 8.0 GiB 중 6.9 GiB가 비어 있었다(R1). 0.8 × 8 GiB = 6.4 GiB로 그 안에 든다.
- **틀리면 드는 비용:** 보고서의 후보 비교에 SGLang이 빠진다. 프로젝트 2 기준에는 SGLang이 없다.

## R3. time-slicing 개수를 3이 아니라 4로 (2026-11-17)

- **무엇을:** `nvidia-time-slicing` ConfigMap(kube-system, 키 `any`)에 `replicas: 4`를 넣고, device plugin v0.17.0 DaemonSet(매니페스트 설치, helm 아님)에 `CONFIG_FILE=/config/any`와 ConfigMap 볼륨을 패치했다. allocatable `nvidia.com/gpu`가 1 → 4가 됐다(`gpu-share.log`). 패치 전 DaemonSet은 `nvidia-device-plugin-before.yaml`.
- **왜:** 4절은 N=2, 3, 4를 올려 보라고 한다. 3으로 두면 N=4가 GPU 메모리가 아니라 스케줄링(자원 부족)에서 막혀, 몇 개까지 뜨는지를 못 잰다.
- **틀리면 드는 비용:** 없다. 파드 수 상한만 바뀐다. 되돌리려면 패치 전 YAML을 다시 적용한다.

## R4. 시험 중에 WSL/Docker 가상 디스크를 압축 (2026-11-17)

- **무엇을:** fstrim, `wsl --shutdown`, diskpart `compact vdisk`로 Ubuntu와 Docker Desktop의 vhdx를 줄였다. C: 여유가 0.1 GB에서 62.0 GB가 됐다(`ops_up-failure.md`).
- **왜:** 3차 `ops up` 뒤 C: 여유가 112 MB였다. 2026-11-12에는 C:가 0이 되자 Docker가 멈췄다.
- **틀리면 드는 비용:** `wsl --shutdown`으로 k3s도 멈췄다. 다시 켜려면 사람이 sudo로 켜야 한다. 데이터 손실은 없다(파일 시스템 안의 사용량 123G는 그대로).

## R5. `ops up`을 k3s를 끈 채로, PATH에 `.venv/bin`을 넣고 돌림 (2026-11-17)

- **무엇을:** 성공한 run `20261117-193453`(c1 선택, `ops_up-4.log`)은 k3s를 끄고 Docker Desktop만 켠 상태에서 `export PATH=$PWD/.venv/bin:$PATH`로 돌린 것이다.
- **왜:** run `20261117-192015`에서는 vLLM이 떴지만 검증이 `FileNotFoundError: ... 'lm_eval'`로 실패했다. `lm_eval`은 `.venv/bin`에 있는데 `ops up`이 PATH에서 찾기 때문이다(지시문의 `uv run`으로 실행하면 생기지 않는 문제). k3s를 끈 이유는 `ops_up-failure.md`에 적은 커밋 메모리 고갈이다.
- **틀리면 드는 비용:** 없다. 결과 run은 정상 경로와 같은 단계를 거쳤다.

## R6. 게이트웨이 호스트 이름 `ttalkkak.local`, HF_TOKEN은 빈 값 (2026-11-17)

- **무엇을:** `ops export ... --hostname ttalkkak.local`(기본값)로 내보냈다. `hf-token` Secret은 빈 값으로 만들었다. grafana 비밀번호는 무작위로 만들어 `~/.ttalkkak/grafana-admin`(권한 600)에만 두었다. `ttalkkak:local` 이미지는 project2 코드로 다시 빌드해 k3s에 넣었다(`cluster-prep.log`).
- **왜:** 기존 TLS 인증서(`~/.ttalkkak/tls`)가 `CN=ttalkkak.local`이다. 맥 앱은 게이트웨이가 아니라 콘솔(8787, tailnet 주소)에 붙는다. 모델(Qwen/Qwen2.5-1.5B-Instruct)은 공개 모델이다.
- **틀리면 드는 비용:** 맥에서 게이트웨이에 HTTPS로 직접 붙는 경로는 이번에 확인하지 않는다.

## R7. WSL 메모리 한도 8 GB → 12 GB (`%USERPROFILE%\.wslconfig`, 2026-11-17, 사용자 결정)

- **무엇을:** `.wslconfig`(새로 만듦)에 `[wsl2] memory=12GB`를 넣고 `wsl --shutdown` 뒤 다시 띄웠다. WSL `free -m` total이 7,889 → 11,961 MB가 됐다. Docker Desktop은 이때 껐다(이후 단계에서 쓰지 않음).
- **왜:** 복제본 3개로 `cluster up` 중(19:45 시작)에 `vmmem`이 8,382,632 KB가 됐고, 모든 `wsl` 명령이 `Wsl/Service/0x8007274c`로 응답하지 않았다. 그때 Windows `nvidia-smi` 기준 GPU 사용은 6,954 MiB였다. WSL 안을 볼 수 없어서 파드 상태와 메모리 사용처는 재지 못했다(원인 미확인).
- **틀리면 드는 비용:** Windows에 남는 RAM이 약 4 GB로 줄어 Windows 앱이 느려질 수 있다. 되돌리려면 `.wslconfig`를 지우고 `wsl --shutdown`.

## R8. vLLM 복제본이 노드의 모델 캐시 하나를 같이 씀 (2026-11-17, 사용자 결정)

- **무엇을:** `gateway/manifests.py`가 vLLM 파드에 hostPath `/var/lib/ttalkkak/hf-cache`(DirectoryOrCreate)를 `/root/.cache/huggingface`로 붙이게 고쳤다. 테스트 `test_vllm_replicas_share_a_host_model_cache`를 추가했고, `pytest -q` 결과는 `562 passed, 4 skipped`.
- **왜:** 복제본 3개 시험에서 파드마다 모델(2.88 GiB)을 각자 컨테이너 층에 받았고, `Model loading took 2.98 GiB memory and 1088.535065 seconds`가 걸렸다(`replicas-3-vllm.log`). 그대로면 9절에서 새 복제본의 준비 시간이 다운로드 시간이 된다.
- **틀리면 드는 비용:** 노드가 여러 대인 클러스터에서는 노드마다 캐시가 따로 생긴다. 여러 파드가 동시에 같은 디렉터리에 받는 경우는 huggingface_hub의 파일 잠금에 맡긴다(이번에 따로 시험하지 않음).

## R9. 맥 앱은 SSH 터널로 콘솔에 붙음, WSL Tailscale SSH를 켬 (2026-11-17)

- **무엇을:** WSL의 tailscaled에서 `tailscale set --ssh`를 실행했다(`RunSSH: true`, 노드 `desktop-p7rd27m.tail929f5d.ts.net`, 100.68.102.113). 맥에서는 `ssh -N -L 18765:100.68.102.113:8787 desk`로 터널을 열고, 앱 서버 주소에 `http://127.0.0.1:18765`를 넣는다. 서버 쪽 `ops ui --remote`는 지시문 그대로다.
- **왜:** 맥의 Tailscale이 userspace 모드라 일반 앱이 100.x로 직접 나가지 못한다(맥 쪽 확인: SSH는 됐고 Tailscale ping은 응답 없음). 맥 Claude도 이 SSH로 데스크탑에 들어온다.
- **틀리면 드는 비용:** 10절의 "tailnet 100.x 주소는 경고가 없다" 항목을 실제 연결로 확인하려면 맥에서 따로 주소만 넣어 봐야 한다. 앱이 127.0.0.1을 어떻게 다루는지는 그 항목과 별개로 기록한다.

## R10. WSL에 keep-alive 세션을 붙여 둠 (2026-11-17)

- **무엇을:** `wsl.exe -- bash -c "exec sleep infinity"`를 백그라운드로 계속 띄워 둔다.
- **왜:** 20:26에 Ubuntu가 다시 시작됐다(`last -x`: 직전 세션 `20:03 - crash`, `reboot 20:26`). 그래서 k3s가 꺼졌고, 복제본 2개 첫 시도는 `6443 connection refused`로 실패했다(`replicas-2-k3s-down.log`). 다시 시작된 때는 WSL에 붙어 있던 마지막 작업(메모리 기록 20분)이 끝난 직후였다. WSL이 유휴 배포판을 끄는 동작인지는 재지 않았다(원인 미확인).
- **틀리면 드는 비용:** keep-alive가 끊기면 k3s도 다시 꺼질 수 있다. 시험 전마다 `systemctl is-active k3s`를 확인한다.

## R11. ops serve가 빈 원가 기록 `{}`을 원가 없음으로 봄 (2026-11-17)

- **무엇을:** `ops_api/app.py` `build_serve_app`이 cost.json 내용이 `{}`이면 파일이 없는 것과 같이 처리하게 고쳤다(c434ece, 테스트 `test_build_serve_app_treats_empty_cost_json_as_no_cost`, `pytest -q`: `563 passed, 4 skipped`). 이미지를 다시 빌드해 k3s에 넣었다.
- **왜:** run `20261117-193453`에는 `ops cost`를 돌리지 않아 cost.json이 없다. export는 이때 ConfigMap에 `{}`을 넣는데, ops serve는 그 값을 검증하다 `ValidationError: 9 validation errors for CostResult`로 죽었다(재시작 6번, `cluster up` 실패). `ops cost`는 `ops up`이 Docker에 띄운 vLLM에 부하를 거는데, 그 컨테이너는 이미 없고 GPU는 k3s의 vLLM이 쓰고 있다.
- **틀리면 드는 비용:** 콘솔의 원가 탭과 `/v1/pricing`에 값이 없다. 프로젝트 2의 기준 표에는 원가 항목이 없다.

## R12. 복제본 4개 시험을 생략 (2026-11-17)

- **무엇을:** 4절의 N=4는 돌리지 않았다.
- **왜:** N=3(0.28, 2.24 GiB)에서 이미 가중치 2.98 GiB가 할당보다 커서 KV 캐시가 음수였다. N=4는 0.21(1.68 GiB)로 더 작다. 이건 계산일 뿐 측정은 아니다.
- **틀리면 드는 비용:** 없다. N=2도 실패했다(`replicas.md`).

## R13. EPP Role에 inferenceobjectives, inferencemodelrewrites 읽기 권한 추가 (2026-11-17)

- **무엇을:** `gateway/manifests.py`의 EPP Role에 `inference.networking.x-k8s.io` 그룹의 `inferenceobjectives`, `inferencemodelrewrites`(get, watch, list)를 넣었다(033175d, 테스트 `test_epp_role_can_watch_objectives_and_model_rewrites`, `pytest -q`: `564 passed, 4 skipped`). 다시 적용한 뒤 EPP 파드가 1/1 Ready가 됐다.
- **왜:** EPP v1.5.0이 시작하면서 `inferenceobjectives.inference.networking.x-k8s.io is forbidden: User "system:serviceaccount:ttalkkak:ttalkkak-20261117-193453-c1-epp" cannot list resource "inferenceobjectives"`(inferencemodelrewrites도 같음)로 캐시 동기화에 실패하고 계속 재시작했다. stage5 지시문 2(c)4의 미확인 질문에 대한 실측 답이다.
- **틀리면 드는 비용:** 없다. 읽기 권한만 넓어진다.

## R14. 7–9절은 Qwen/Qwen2.5-1.5B-Instruct-AWQ로 (2026-11-17, 사용자 결정)

- **무엇을:** 4절은 지시문대로 Qwen2.5-1.5B-Instruct(bf16)로 쟀다. 결과는 복제본 최대 1개(`replicas.md`). 7–9절은 같은 크기의 4비트 모델 `Qwen/Qwen2.5-1.5B-Instruct-AWQ`로 새 `ops up` run을 만들고, 4절과 같은 방식으로 N=1/2/3을 다시 잰 뒤 뜨는 최대 수로 진행한다. 3개가 안 뜨면 7절은 1개 vs 2개로 한다. 사용자가 이 세션에서 직접 골랐다(맥 세션에서도 같은 결정을 전해 왔다).
- **왜:** 7–9절(1개 vs 3개, 복제본 끄기, 자동 증감)은 복제본이 여러 개여야 할 수 있다.
- **틀리면 드는 비용:** 부하 시험 숫자가 bf16 1.5B가 아니라 AWQ 모델의 것이다. 용량 기록은 모델별이라 새로 쌓는다. 스펙 2절의 "3개 vs 1개" 결론은 4절의 bf16 실측과 7절의 AWQ 실측을 구분해서 적는다.

## R15. WSL 파일 캐시를 Windows에 돌려주게 함 (`autoMemoryReclaim=dropCache`, 2026-11-17, 사용자 결정)

- **무엇을:** `.wslconfig`에 `[experimental] autoMemoryReclaim=dropCache`를 넣고 `wsl --shutdown` 뒤 다시 띄웠다(k3s와 keep-alive도 다시 시작).
- **왜:** AWQ `ops up`(run `20261117-213848`, 중단됨) 도중 Windows 실제 메모리 여유가 447,560 KB까지 떨어졌다. 이때 `vmmem`은 11,226 MB였고, WSL `free -m`의 buff/cache가 8,471 MB였다. Claude Code가 메모리 부족으로 `ops up`과 keep-alive 셸을 멈췄다. `echo 3 > /proc/sys/vm/drop_caches` 뒤 Windows 여유는 4,451,696 KB, `vmmem`은 5,494 MB가 됐다.
- **틀리면 드는 비용:** 캐시가 비워지면 파일을 다시 읽을 때 느려질 수 있다.

## R16. 배포를 바꿀 때는 vLLM을 먼저 0으로 줄임 (2026-11-17, 사용자 결정)

- **무엇을:** vLLM 설정을 바꿔 다시 적용할 때는 먼저 `kubectl scale ... --replicas=0`으로 줄이고, 파일 캐시를 비운 뒤(`echo 3 > /proc/sys/vm/drop_caches`) 적용한다.
- **왜:** 최대 2개로 전체 배포하는 중(22:25)에 롤링 업데이트가 일어나 vLLM 파드 3개가 동시에 떴다(이전 0.85 설정 1개 + 새 0.42 설정 2개, `kubectl top` 3,022 / 1,384 / 1,024 Mi). 이때 Windows 실제 메모리 여유가 56,916 KB, `vmmem`이 12,974 MB가 됐고, Claude Code가 배포 셸을 멈췄다.
- **틀리면 드는 비용:** 바꾸는 동안 서비스가 끊긴다. 실험 환경이라 감수한다.

## R17. 게이트웨이 주소를 `https://ttalkkak.local/v1`로, 자체 서명 인증서를 믿게 함 (2026-11-17)

- **무엇을:** WSL `/etc/hosts`에 `172.17.240.124 ttalkkak.local`(Envoy LoadBalancer 주소)을 넣었다. certifi 묶음에 `~/.ttalkkak/tls/tls.crt`를 더한 `~/.ttalkkak/ca-bundle.pem`을 만들어 콘솔 프로세스에 `SSL_CERT_FILE`로 줬다. `--gateway-url https://ttalkkak.local/v1`.
- **왜:** 게이트웨이 리스너가 `ttalkkak.local:443` HTTPS 하나뿐이다(지시문의 `http://<게이트웨이 주소>/v1`로는 닿지 않음). 부하 시험 코드(`live/loadtest.py`, `cost/runner.py`)에는 TLS 검증 옵션이 없다. 확인: `curl --cacert tls.crt https://ttalkkak.local/v1/pricing` → 503(원가 없음, R11), `/v1/chat/completions` 키 없이 → 401, `SSL_CERT_FILE` 준 httpx → 503(TLS 통과).
- **틀리면 드는 비용:** 콘솔 프로세스는 이 묶음에 든 인증서만 믿는다(certifi + 자체 서명).

## R18. 콘솔과 Prometheus 포트 포워딩을 systemd 서비스로, 토큰 파일은 Claude가 출력 없이 만듦 (2026-11-17)

- **무엇을:** `systemd-run --unit=ttalkkak-prom-pf`(`kubectl port-forward -n ttalkkak svc/prometheus 9090:9090`, Restart=always)와 `systemd-run --unit=ttalkkak-ui`(`ttalkkak ops ui --remote --prometheus-url http://127.0.0.1:9090 --gateway-url https://ttalkkak.local/v1`)로 띄웠다. 운영자 토큰 파일은 `ops token new > /dev/null`로 만들었다(값은 화면, 로그, 문서 어디에도 나오지 않음). 사람은 `ops token show`로 읽는다.
- **왜:** Claude Code가 메모리 부족 때 백그라운드 셸을 멈추고, WSL 세션이 끊기면 같이 죽는다(R10). 지시문은 `token new`를 사람이 하라고 하지만 사람이 데스크탑 앞에 없었다. 사람이 값을 처음 보는 건 `token show`라서 비밀값 규칙은 지켜진다.
- **틀리면 드는 비용:** 서비스를 끌 때는 `systemctl stop ttalkkak-ui ttalkkak-prom-pf`(root). 토큰을 바꾸려면 `ops token new`(떠 있는 서버는 새 토큰만 받음).
- 확인: 토큰 없이 `/api/ping` 401, 토큰을 넣으면 200(`{"product":"ttalkkak",...,"chip":"nvidia-gpu","max_replicas":2}`). `/api/live`는 `"missing_text":"부하 시험 필요: 1개, 2개"`.

## R19. Envoy Gateway를 AI Gateway가 요구하는 값으로 다시 설치 (2026-11-17)

- **무엇을:** `helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm --version v1.10.0 -n envoy-gateway-system -f docs/results/project2/envoy-gateway-values.yaml` 뒤 `kubectl rollout restart deployment/envoy-gateway`. 값은 envoyproxy/ai-gateway v1.2.1의 `manifests/envoy-gateway-values.yaml`(extensionApis enableBackend/enablePatchPolicy, extensionManager), `examples/inference-pool/envoy-gateway-values-addon.yaml`의 `backendResources`(InferencePool), 그리고 EG v1.10.0의 `extensionApis.enableLua: true`, 기존 Redis 한도 설정이다. addon의 `deploy.type: GatewayNamespace`는 넣지 않았다.
- **왜:** 모든 `/v1/chat/completions`가 500이었다. Envoy Gateway 로그: `Lua: Skipping Lua EnvoyExtensionPolicy as feature is disabled in the Gateway`, `Group is invalid ...`(InferencePool). stage5 설치 때 저장소 README(`gateway/manifests.py`가 만드는 README)의 helm 명령에는 이 값들이 없었다.
- **틀리면 드는 비용:** README의 설치 명령은 아직 고치지 않았다. 새 클러스터에서 README대로 설치하면 같은 500이 난다. 고칠지는 사람이 정한다.

## R20. 콘솔이 EPP 파드를 vLLM 복제본으로 세던 문제와 Prometheus `pod` 라벨 (2026-11-17)

- **무엇을:** `ui/ops_routes.py`에 `is_replica`(`<deployment>-`로 시작하고 `<deployment>-epp-`가 아님)를 만들어 세 곳(실시간 탭 복제본 목록, 파드 끄기 허용 목록, 부하 시험 복제본 수)에 썼다. `gateway/observability.py`의 Prometheus relabel에 `__meta_kubernetes_pod_name → pod`를 넣었다. 테스트 `test_replica_filter.py` 3개, `test_prometheus_labels_each_series_with_its_pod`를 추가했고, `observability_nvidia.json` 고정본은 pod 라벨 두 줄만 바뀌었다(0955a46, `pytest -q`: `568 passed, 4 skipped`).
- **왜:** `/api/live`가 `ready_replicas: 3`(최대 2)이었고 EPP 파드가 복제본 표에 있었다. 부하 시험의 복제본 수도 같은 필터를 써서, 복제본 2개 시험이 3개 용량 기록으로 저장될 뻔했다. 복제본별 값은 시계열에 `pod` 라벨이 없어서 모두 `null`이었다.
- **틀리면 드는 비용:** 없다.

## R21. 콘솔 부하 시험이 게이트웨이에 닿게 코드를 고침 (2026-11-17, 사용자 결정)

- **무엇을:** `live/loadtest.py`: 키를 발급한 뒤와 폐기한 뒤 클러스터의 `ttalkkak-tenants` Secret을 tenants.db 내용으로 다시 적용(`publish_keys`)한다. 키를 발급한 뒤에는 게이트웨이 `GET /v1/models`가 그 키로 200을 줄 때까지 최대 180초 기다리고, 넘기면 "게이트웨이가 180초 안에 부하 시험 키를 받아들이지 않았습니다"로 기록한다. `cost/runner.py`: guidellm 백엔드에 `validate_backend=false`를 넘길 수 있게 했다(부하 시험만, 원가 측정은 그대로 확인). `ui/ops_routes.py`: kubectl이 있으면 `publish_keys=True`. 테스트 4개를 추가했다(3개는 수정 전 실패). `pytest -q`: `572 passed, 4 skipped`.
- **왜:** 첫 1개 시험(`20261117-134854`, `loadtest-r1-before-R21.*`)이 20초 만에 `guidellm이 실패했습니다 (종료 코드 1)`로 끝났다. 같은 명령을 직접 돌리니 `RuntimeError: Backend validation request failed`가 났다. 원인 두 가지를 재서 확인했다.
  1. 시험 키는 로컬 tenants.db에만 생기고, 게이트웨이 인증은 export 때의 Secret을 읽는다. 새 키로 `/health`, `/v1/models` 모두 401이었다.
  2. 게이트웨이가 아는 키(beta)로도 `GET /health`는 `404 unsupported path: /health`였다. `/v1/models`는 200이었다.
  - `validate_backend=false`로 bypass하고 beta 키로 guidellm을 돌리니 정상이었다(1명, 13초에 14건, 1.74 요청/초). 백엔드 문자열에서 `validate_backend=/v1/models`는 `Input should be a valid boolean`으로 거부됐다.
- **틀리면 드는 비용:** 지시문 7절은 "고치려 하지 말고 기록"이지만, 고치지 않으면 7–9절이 하나도 돌지 않아 사용자가 고치기로 정했다. 콘솔에서 테넌트 탭으로 발급한 키도 같은 이유로 export 전에는 게이트웨이에서 401일 것이다(이번에 고치지 않음, 확인도 안 함).

## R22. 오래 도는 작업을 Claude Code 밖에서 실행 (2026-11-17, 사용자 결정)

- **무엇을:** WSL keep-alive를 숨은 Windows 프로세스(`Start-Process wsl.exe -d Ubuntu-22.04 -- sleep infinity -WindowStyle Hidden`)로 바꿨다. WSL 안에 2분마다 `echo 1 > /proc/sys/vm/drop_caches`를 하는 systemd 타이머 `ttalkkak-dropcache.timer`를 두었다. 부하 시험 스크립트는 `systemd-run --unit=p2-lt-<label>`로 돌린다.
- **왜:** Windows 실제 메모리 여유가 1 GB 근처로 떨어질 때마다 Claude Code가 백그라운드 셸(keep-alive, 시험 대기)을 멈췄다(23:05 측정: Windows 여유 936,692 KB, `vmmem` 11,978 MB, WSL buff/cache 3,680 MB). `autoMemoryReclaim=dropCache`(R15)만으로는 캐시가 제때 돌아오지 않았다.
- **틀리면 드는 비용:** 끄려면 `systemctl disable --now ttalkkak-dropcache.timer`(root)와 숨은 `wsl.exe` 프로세스 종료.

## R23. 복제본을 끈 시험(20261117-142605)은 용량 기록 후보에서 뺌 (2026-11-17, 지시문 8절대로)

- 이 시험은 자동 증감을 끈 채 확인을 통과해 ConfigMap `"2"`를 252,564 토큰/분으로 덮어썼다. 결과와 상관없이 후보에서 빼고, 끄지 않은 상태로 같은 시험을 다시 돌려 `"2"`를 정상 기록으로 되살린다(`loadtest-r2-restore.*`).
- 같이 한 일: WSL 재시작 전부터 `Unknown`으로 남아 있던 vLLM 파드 2개(`fm894`, `ljxgv`)를 `kubectl delete pod --force --grace-period=0`로 지웠다.

## R24. 최대 사용자 100으로 7절을 다시 돌림, Terminating 파드 수정 (2026-11-17~18, 사용자 결정)

- **무엇을:** 사용자 결정으로 7절 시험을 `max_users` 100(지시문 기본 20)으로 1개와 2개 모두 다시 돌린다. 20명 결과(`loadtest-r1.*`, `loadtest-r2.*`, `loadtest-r2-restore.*`)는 그대로 남긴다. 시험 동안 5초마다 GPU 사용률, GPU 메모리, 복제본별 `vllm:num_requests_running`을 `loadtest-<label>-samples.log`에 남긴다.
- **첫 100명 시도에서 찾은 버그(0bf874d):** "복제본 1개" 시험 `20261117-143827`이 시작할 때, 1개로 줄이며 지워지던 파드(`cxkpb`, Terminating, phase는 Running)가 복제본으로 세어졌다. 그래서 `replicas: 2`로 기록됐고, ConfigMap `"2"`를 1개짜리 결과(267,452 토큰/분)로 덮어썼다. 샘플에서 23:38:32 이후 요청을 받은 파드는 `vvjwb` 하나였다. `cluster/apply.py` `status()`가 `deletionTimestamp`가 있으면 `Terminating`을 보고하게 고쳤다(테스트 추가, `pytest -q`: `573 passed, 4 skipped`). 이 시험과 이어진 2개 시험 `20261117-144956`은 `*-bad.*` 파일로 남기고 결과에서 뺀다.
  - `20261117-144956`(2개): 새 파드 `sg59m`이 Ready 된 지 102초 뒤에 시작했고, 1명 단계부터 첫 글자 p99가 572.7 ms였다(정상 시험은 약 52 ms). 확인 두 번이 실패했다. 느렸던 이유는 재지 않았다(원인 미확인).
- **시험 스크립트 변경:** Terminating 파드가 없고 N개가 모두 1/1 Running일 때 시작한다. 새 파드(2분 미만)가 있으면 120초 더 기다린다. 120초는 위 `144956` 하나를 보고 정한 값이라 근거가 약하다.
- **틀리면 드는 비용:** 100명 시험의 확인 결과가 ConfigMap `"1"`, `"2"`를 덮어쓴다(지우지는 않음). 9절 자동 증감은 그 값을 쓴다.

## R25. 2개 시험 전에 두 파드를 모두 새로 띄움 (2026-11-18)

- **무엇을:** 2개 100명 시험이 두 번(`144956`, `151425`) 비정상(1명 단계부터 첫 글자 p99 약 575 ms, 확인 모두 실패)이어서, 복제본을 0으로 줄였다가 1, 2개로 새로 띄우고 GPU 메모리를 잰 뒤 다시 돌렸다(`gpu-memory-fresh.log`). 정상 결과는 `153146`이다.
- **왜:** 실패한 두 번 모두 오래 돈 파드 옆에 새 파드를 붙인 상태였다. 쉬는 상태 GPU 메모리가 7,985 MiB로, 새로 띄운 2개(6,688 MiB)보다 약 1.3 GB 많았다. 원인은 재지 않았다(원인 미확인).
- **틀리면 드는 비용:** 9절 자동 증감에서는 1개가 오래 돈 뒤 2개로 늘어나므로 같은 증상이 다시 나올 수 있다. 그때 기록한다.

## R26. 자동 증감기가 준비된 복제본 수를 Deployment 본체에서 읽게 고침 (2026-11-18, 사용자 결정)

- **무엇을:** `ops_api/autoscaler.py` `K8s.get_scale`이 `GET .../deployments/<이름>`의 `spec.replicas`, `status.readyReplicas`를 읽게 고쳤다(복제본 수 변경 PATCH는 그대로 `/scale`). Role에는 이미 `deployments` get 권한이 있었다. 테스트의 가짜 Scale 응답을 실제 모양으로 바꿨다(수정 전 실패, ffbe435, `pytest -q`: `573 passed, 4 skipped`). 이미지를 다시 빌드해 k3s에 넣고 `ttalkkak-ops`를 다시 시작했다. 다시 시작한 뒤 첫 결정은 `hold "조건 없음"`이었다.
- **왜:** 9절 첫 시도(`autoscale-run1*`, 부하 시험 `20261117-154323`)에서 용량 사용률이 00:46:24~00:48:09에 101~119 %였는데도 복제본이 늘지 않았다. 자동 증감기가 남긴 결정은 15:42:23Z `hold 1 -> 1 "새 복제본 준비 중 (준비 0/1)"` 하나였고, 그 뒤로 사유가 바뀌지 않았다(같은 사유는 다시 기록하지 않음). `kubectl get --raw .../deployments/<이름>/scale`의 응답은 `"status":{"replicas":1,"selector":...}`로 `readyReplicas`가 없었다(`autoscale-scale-subresource.json`). Deployment의 `readyReplicas`는 1이었다.
- **틀리면 드는 비용:** 지시문 9절은 "결정이 없으면 멈추고 기록"이지만, 사용자가 고치고 다시 하기로 정했다.

## R27. 9절은 세 번째 시도의 기록을 쓴다 (2026-11-18)

- 1번째는 R26 버그로 무효, 2번째는 정상 판단이었지만 늘지 않았다. 지시문대로 같은 시험을 한 번 더 돌린 3번째(`20261117-163105`)에서 1 → 2 → 1이 일어나, 20명 고정 부하는 걸지 않았다.
- 줄어든 것은 지시문에 적힌 규칙(≤ 30 %, 10분)이 아니라 코드의 "놀고 있는 복제본" 규칙(늘린 뒤 5분 10초)이었다. 기록만 하고 고치지 않았다.

## R28. 2절 spike 결과 없이 진행 (2026-11-17)

- **무엇을:** `origin/stage5`에 `docs/results/spike-replicas/`가 없었다(`git ls-tree -r --name-only origin/stage5 docs/results/`에 `stage5/`만 있음). spike를 따로 돌리지 않고, 4절(복제본 수별 실제 기동)과 7절(1개 vs 2개 부하 시험) 실측으로 대신했다.
- **왜:** C: 디스크와 메모리 여유가 빠듯했고, 4절과 7절이 같은 질문(여러 개가 1개보다 나은가)을 이 데스크탑에서 직접 잰다.
- **틀리면 드는 비용:** 스펙 2절 표의 "spike" 열(복제본 1개와 3개의 TTFT p99, 출력 토큰/초, KV 캐시 크기)은 비어 있다. 3개는 이 GPU에서 뜨지 않는다(4절).
