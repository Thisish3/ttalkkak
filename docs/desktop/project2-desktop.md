# 프로젝트 2 데스크탑 실기 검증 지시문 (몰려도 버티기)

**대상:** Windows 10 + WSL2 + Docker Desktop + k3s, RTX 3060 Ti 8GB, Tailscale 호스트 이름 `desk`. 데스크탑의 Claude가 이 문서를 위에서 아래로 따라 하고, 맥 앱을 쓰는 확인은 사람이 맥에서 한다.

## 비밀값 규칙 (먼저 읽기)

- 운영자 토큰과 API 키는 채팅, 로그, 결과 문서, ledger, 커밋 어디에도 붙여 넣거나 출력하지 않는다. 명령 출력에 키가 찍혔다면 그 부분을 지운 뒤 문서에 옮긴다.
- 운영자 토큰은 사람이 데스크탑 터미널에서 `ttalkkak ops token show`(처음이면 `ttalkkak ops token new`)로 직접 읽고, 맥 앱에 직접 입력하거나 붙여넣는다(비밀번호 관리자로 옮겨도 된다). Claude는 `test -s ~/.ttalkkak/console-token`처럼 파일이 있는지만 확인한다.
- 확인용 API 키는 부하 시험 전용 테넌트 `ttalkkak-loadtest`(청구서에서 빠짐)로 발급하고, 출력은 화면에 찍지 말고 파일로 받아 쓴 뒤 폐기한다(7절 4번).

**규칙**
- 추측하지 않는다. 재지 않은 원인은 "원인 미확인"이라고 쓴다.
- 이 문서와 다르게 한 결정은 `docs/project2-ledger.md`에 `R1`부터 번호를 붙여 적는다(무엇을, 왜, 틀리면 드는 비용). 파일이 없으면 만든다.
- 모든 결과 파일은 `docs/results/project2/`에 두고 커밋한다. push는 사용자에게 물어본 뒤에 한다.
- 명령은 저장소 루트에서 `.venv/bin/ttalkkak ...`(또는 `uv run ttalkkak ...`)로 돌린다. 플래그는 `--help`로 먼저 확인한다.
- 예상 시간: 부하 시험 한 번은 최대 약 8분(탐색 최대 10단계 × 30초 + 열린 부하 확인 최대 3번 × 60초)이다. 보통은 그보다 짧다.

## 0. 이번에 확인하는 코드의 동작 (읽고 시작)

- **부하 시험** (`src/ttalkkak/live/loadtest.py`): guidellm 0.8.1의 goodput 탐색.
  - 탐색: `kind=goodput,target_attainment=0.95,initial_streams=1,max_streams=<최대 사용자>,max_probes=10,tolerance=0.2,warmup=5`. 단계(probe)마다 `max_duration` 30초, 처음 5초는 워밍업이라 측정에서 빠진다. 데이터는 입력 256 / 출력 128 토큰 합성 텍스트. 첫 글자 목표 기본 1000 ms, 달성률 목표 95 %.
  - 열린 부하 확인: 찾은 수준에서 잰 요청 속도(성공 요청 ÷ 측정 시간)의 **0.75배**를 60초 동안 포아송으로 보낸다(워밍업 5초). 못 지키면 한 단계 낮은 통과 수준으로 내려가 다시 확인하고, 최대 3번까지 한다.
  - 용량 = 확인을 통과한 실행이 실제로 처리한 토큰/분. 확인이 모두 실패하면 "확인 실패" 기록이 되고 쓰지 않는다. 같은 모델에 확인된 기록이 있으면 확인 실패한 새 시험은 그 기록을 덮지 않는다.
  - 무릎(knee): 탐색 단계가 5개 이상일 때 guidellm의 무릎 분석으로 처리량이 꺾이는 동시 사용자 수를 같이 남긴다.
  - 기록: `runs/loadtests/<시각>.json`(필드 `stages`, `stop_reason`, `confirms`, `capacity_streams`, `capacity_tpm`, `knee`, `no_capacity_reason`, `error`)과 ConfigMap `ttalkkak-capacity`(복제본 수마다 하나, 자동 증감을 끈 시험만 저장). 부하 시험 키는 끝나면 폐기되고, 사용량은 청구서에서 빠진다.
- **용량 기록은 복제본 수마다 따로**: 자동 증감은 현재 복제본 수의 기록만 쓴다. 기록이 없는 수는 콘솔에 "부하 시험 필요: 2개, 3개"로 보이고 자동 증감은 거기서 멈춘다. 추정값은 쓰지 않는다. 그래서 최대 복제본 수까지 각각 한 번씩(자동 증감 끈 채) 시험해야 한다. 값은 `kubectl get configmap ttalkkak-capacity -n ttalkkak -o jsonpath='{.data.capacity\.json}'`로 읽는다(JSON 안의 키가 복제본 수).
- **`ttalkkak-ops` Deployment는 Recreate 전략**이다. 롤아웃 때 몇 초 동안 ops API가 끊기는 것이 정상이다(자동 증감 루프가 두 개 겹치지 않게 하려는 것).
- **수동 복제본 수 조절은 앱에서 뺐다.** 복제본 수는 자동 증감이 바꾸거나, 아래 `kubectl scale`로 직접 바꾼다. 복제본 끄기(파드 삭제)는 앱의 복제본 표에서 한다.

## 1. 브랜치와 환경

```bash
git fetch origin && git checkout project2 && git pull --ff-only
uv sync --extra ops --extra measure   # 이미 했으면 건너뜀
.venv/bin/ttalkkak ops --help
```

## 2. spike 결과 확인

spike 결과는 `stage5` 브랜치에 커밋돼 있고 `project2`에는 `docs/results/spike-replicas/`가 없다. `git fetch origin stage5 && git ls-tree -r --name-only origin/stage5 docs/results/spike-replicas/`로 찾고, `git show origin/stage5:docs/results/spike-replicas/<파일>`로 읽는다. 거기에도 없으면 `docs/desktop/spike-replicas.md`를 먼저 따라 하거나, 건너뛰는 결정을 ledger에 적는다. 복제본 1개와 3개의 TTFT p99, 출력 토큰/초, KV 캐시 크기를 `docs/results/project2/summary.md`에 옮겨 적는다. 스펙 2절 표의 결론(3개가 1개보다 빠른가)은 이 숫자로만 쓴다.

## 3. GPU 한 장을 여러 파드가 나눠 쓰기

방법 A (기본, `--gpu-share resource`): NVIDIA device plugin의 time-slicing. 플러그인 설정에 다음을 넣어 `nvidia.com/gpu`를 3개로 보이게 한다(설정 키는 플러그인 버전 문서 기준으로 확인).

```yaml
version: v1
sharing:
  timeSlicing:
    resources:
      - name: nvidia.com/gpu
        replicas: 3
```

적용 방법: 플러그인이 이미 있는지 먼저 본다(`kubectl get pods -A | grep -i nvidia-device-plugin`). 없으면 설치부터 한다. 위 YAML을 ConfigMap(예: `nvidia-time-slicing`, 키 `any`)으로 만들고 플러그인이 그 ConfigMap을 읽도록 설정(helm이면 `config.name`, 매니페스트면 `--config-file`/`CONFIG_FILE`)한 뒤 플러그인 파드를 재시작한다. 정확한 방법은 설치한 플러그인 버전의 문서를 따르고, 한 방식이 안 되면 오류 메시지를 적는다.

적용 뒤 확인:

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'   # 3
```

방법 B (A가 k3s에서 안 될 때, `--gpu-share runtime`): 파드가 `nvidia.com/gpu`를 요청하지 않고 `runtimeClassName: nvidia`와 `NVIDIA_VISIBLE_DEVICES`로 GPU를 직접 본다. `nvidia.com/gpu` 칩에서만 된다. 어느 방법을 썼는지, A가 안 됐다면 오류 메시지를 `docs/results/project2/summary.md`와 ledger에 적는다.

## 4. 게이트웨이 배포와 한 GPU에 몇 개까지 뜨는지

```bash
.venv/bin/ttalkkak ops export --run <run> --target gateway --out deploy/ \
  --replicas 3 --max-replicas 3 --autoscale --gpu-share resource --hostname <호스트>
.venv/bin/ttalkkak ops cluster up --out deploy/
.venv/bin/ttalkkak ops cluster status
```

- `--replicas`는 처음 띄울 복제본 수, `--max-replicas`는 GPU 메모리 비율을 나누는 기준이다(`--replicas`는 `--max-replicas` 이하여야 한다). 자동 증감을 켜면 최대 기준으로 비율을 고정한다(예: 3개면 복제본마다 `--gpu-memory-utilization` 약 0.28). 복제본이 1개일 때도 이 비율을 쓴다.
- `--replicas N --max-replicas N`을 N=2, 3, 4로 올려 보며(같은 명령을 `--force`로 다시 내보내고 `cluster up`) 몇 개까지 모두 `Running/Ready`가 되는지, 못 뜨면 어떤 오류로 못 뜨는지 기록한다. 결과 표: 복제본 수 | 복제본당 메모리 비율 | 모두 ready? | 시작 로그의 `GPU KV cache size` | `nvidia-smi` 사용 메모리.
- 가장 큰 성공 수를 이후 단계의 최대 복제본 수로 정한다. 3이 아니면 이후 문서의 3을 그 수로 바꿔 읽고 ledger에 적는다.

## 5. 콘솔 서버(원격)와 맥 앱 연결

**어디서 도는가:** `ops ui --remote`는 `kubectl`이 되는 쪽, 즉 **WSL2 셸**에서 돌린다. `--remote`는 그 셸에서 `tailscale ip -4`가 주는 100.x 주소에만 열기 때문에, Tailscale이 **Windows에만** 깔려 있으면 WSL2 안에서는 그 주소가 없어 시작이 거부된다("tailscale 주소를 찾지 못했습니다"). 먼저 WSL2 셸에서 확인한다.

```bash
tailscale ip -4    # 100.x.x.x가 나와야 한다
```

나오지 않으면 다음 중 하나를 고르고, 무엇을 왜 골랐는지 ledger에 R번호로 적는다(시도한 명령, 오류 메시지, 맥에서 연결됐는지 여부 포함).
1. WSL2 안에 Tailscale을 설치해 같은 tailnet에 올린다(`tailscale up`). 이 경우 `desk`와 별개 노드로 보이고 주소가 다르다. 맥 앱에는 그 주소를 쓴다.
2. 그래도 안 되면 `--host 0.0.0.0`을 직접 준다. **위험:** WSL2 안의 모든 인터페이스에 열리므로, Windows 방화벽에서 8787 포트 접근을 tailnet(100.64.0.0/10)으로만 제한하고, 토큰이 평문 HTTP로 가는 것을 감수한다는 결정을 ledger에 적는다. Windows에서 WSL2로 포트가 전달되는지(맥에서 `curl http://<주소>:8787/`)는 측정으로 확인하고, 안 되면 원인 미확인으로 두고 기록한다.

서버 쪽(두 터미널이 필요하다. `ops ui`는 앞에서 계속 돌므로 별도 터미널이나 `tmux`/`nohup`을 쓰고, Prometheus 포트 포워딩도 살아 있어야 한다):

```bash
# 터미널 1: Prometheus 포트 포워딩 (끄면 실시간 화면이 비게 된다)
kubectl port-forward -n ttalkkak svc/prometheus 9090:9090

# 확인: 게이트웨이를 호스트에서 닿는 주소로 찾는다
kubectl get svc -A | grep -i envoy
test -s ~/.ttalkkak/console-token || echo "토큰이 없다: 사람이 ttalkkak ops token new 를 실행한다"

# 터미널 2
.venv/bin/ttalkkak ops ui --remote --prometheus-url http://127.0.0.1:9090 \
  --gateway-url http://<게이트웨이 주소>/v1
```

- 게이트웨이 서비스가 ClusterIP이면 호스트에서 바로 닿지 않을 수 있다. 그때는 `kubectl port-forward -n <네임스페이스> svc/<이름> 8080:80`처럼 포워딩해 `--gateway-url http://127.0.0.1:8080/v1`을 쓰고, 어떤 서비스를 어떻게 닿았는지 ledger에 적는다.
- `--remote`는 `tailscale ip -4`가 알려 주는 tailnet 주소(100.x.x.x)에만 연다. tailscale이 꺼져 있으면 시작을 거부하고 `--host`를 직접 주라고 한다. `0.0.0.0`은 운영자가 `--host 0.0.0.0`으로 직접 줄 때만 열린다.
- 시작할 때 "tailnet 안에서도 통신은 평문 HTTP이며 토큰이 요청마다 전송됩니다"라는 경고가 stderr로 나온다. 정상이다. 토큰은 `~/.ttalkkak/console-token`에 있고 사람이 `ttalkkak ops token show`로 읽으며 `ttalkkak ops token revoke`로 폐기한다(떠 있는 서버는 즉시 거절).
- 기본 포트 8787, 맥 앱의 서버 주소는 `http://<서버가 연 주소>:8787`.

맥(앱 설치): 저장소 GitHub Actions의 `desktop` 워크플로 실행에서 Artifacts의 macOS용 `.dmg`(Apple Silicon 또는 Intel)를 받는다. 서명하지 않은 빌드다.
1. `.dmg`를 열어 앱을 Applications로 옮긴다.
2. 앱을 우클릭하고 `열기`를 누른다. 막히면 시스템 설정 → 개인정보 보호 및 보안에서 `그래도 열기`를 누른다.
3. 서버 주소에 위 tailnet 주소, 운영자 토큰에는 데스크탑 터미널에서 사람이 `ttalkkak ops token show`로 읽은 값을 직접 넣고(채팅이나 문서에 옮기지 않는다) `연결`.

## 6. 지표 이름 대조와 게이트웨이 접근 로그

1. 질의 상수의 지표 이름을 실제 `/metrics`와 맞춰 본다.
   ```bash
   kubectl get pods -n ttalkkak     # vLLM 복제본 파드 이름 확인
   kubectl port-forward -n ttalkkak <vllm 파드> 8000:8000 &
   curl -s localhost:8000/metrics | grep -E "^vllm:" | cut -d'{' -f1 | sort -u
   ```
   `src/ttalkkak/live/prom.py`의 질의 상수와 하나씩 비교한다. 다르면 코드를 고치고(테스트도) 별도 커밋으로 남기고 ledger에 적는다.
2. 게이트웨이 429와 고객별 토큰을 접근 로그에서 읽는 것이 맞는지: 시험용 테넌트의 한도를 낮추고(`ttalkkak ops tenants set-limit <id> 200`), 그 테넌트 키로 `/v1/chat/completions`를 연속으로 보내 429를 낸다(요청에 쓰는 키는 파일에서 읽어 쓰고 화면에 찍지 않는다). 콘솔의 거절 수와 고객별 한도 소진률을 Envoy 게이트웨이 파드의 접근 로그와 비교한다(`kubectl get pods -A | grep -i envoy`로 파드를 찾고 `kubectl logs -n <네임스페이스> <파드> | grep ' 429 '`). 같은지와 어긋나면 어떻게 다른지를 결과 표에 적는다. 끝나면 한도를 원래대로 돌린다.

## 7. 부하 시험 — 자동 증감을 끄고 복제본 1개 vs 3개

시작 전: 앱 실시간 탭에서 `자동 증감` 스위치를 끈다(`● 자동 증감 켜짐` → `– 자동 증감 꺼짐`, 확인 창에서 `끄기`). 부하 시험 카드의 `자동 증감 켠 채로 시험`은 체크하지 않는다(체크하면 결과가 용량 기록에 저장되지 않는다). `최대 사용자`는 기본 20.

복제본 수를 직접 맞춘다.

```bash
DEP=$(kubectl get configmap ttalkkak-autoscale -n ttalkkak -o jsonpath='{.data.deployment}')
kubectl scale deployment/$DEP -n ttalkkak --replicas=1
kubectl rollout status deployment/$DEP -n ttalkkak
```

1. 복제본 1개: `시작`(확인 창에서 `시작`). 시험 중 앱에 진행 단계(`용량 찾는 중` → `열린 부하로 확인 중`)가 보인다. 끝나면 `runs/loadtests/<id>.json`과 위 `jsonpath` 명령으로 읽은 JSON의 `"1"` 항목을 확인한다. 약 8분 이하.
2. `--replicas=3`(또는 4절에서 정한 최대 수)으로 바꾸고 `rollout status`가 끝나 전부 ready가 된 뒤 같은 시험을 한 번 더 한다.
3. 결과를 `docs/results/project2/loadtest-1vs3.md`에 표로 남긴다. **시험마다 반드시 기록할 것:**
   - 탐색 단계 표(앱의 시험 상세 표와 같은 열): 순서 | 동시 사용자 | 달성률 | 신뢰 구간 | 판정(통과/실패/모름) | 오류율 | 중단 | 토큰/분
   - 중단 사유(`stop_reason`: `converged`, `max_streams_reached`, `objectives_unmet_at_minimum`, `indeterminate_at_minimum`, `max_probes_exhausted`, `constraint_stopped_escalation`)와 앱이 보여 주는 한국어 문구
   - 탐색 후 각 단계의 `determined_requests`(`runs/loadtests/<id>.json`의 `stages[]`). 통과한 단계 중 74개 미만(달성률 목표 0.95에서 이 수보다 적으면 통과해도 신뢰 구간이 판정을 못 한다)이 있으면 그 시험은 판정 불가로 보고, 단계 시간을 늘려 다시 돌린다. 단계 시간은 부하 시험 카드의 "단계 시간" 입력(10~300초, 기본 30초, 준비 구간 5초보다 길어야 함)으로 바꾼다. API로 시작할 때는 `probe_seconds` 필드다. 74개 미만이었던 단계, 다시 돌린 단계 시간, 그 결과를 표와 ledger에 적는다.
   - 열린 부하 확인 결과: 확인 번호별 `확인 통과/확인 실패`, 수준(동시 사용자), 요청 속도, 달성률과 신뢰 구간, 오류율, 첫 글자 p99, 토큰/분. 최종 `confirmed`가 true인지 false인지. true이면 `capacity_tpm`은 확인에서 실제로 처리한 토큰/분이다. 모든 확인이 실패(`confirmed` false)이면 `capacity_tpm`은 탐색에서 나온 확인되지 않은 값이므로 용량으로 적지 않는다. 같은 기록의 `no_capacity_reason`, `error`, 그리고 ConfigMap 항목의 `unconfirmed_attempt`도 있으면 적는다
   - 무릎: 동시 사용자 수와 출력 토큰/초(없으면 이유)
   - 동시 사용자 수별 첫 글자 p99 곡선(앱의 겹쳐 그린 그래프, 1개와 3개 한 그래프)을 스크린샷으로
   - 그 밖에 시험이 끝난 화면(탐색 표, 확인 결과, 무릎)도 `docs/results/project2/app/`에 스크린샷으로 남긴다. 각 단계(3, 4, 7, 8, 9절)의 앱 화면 스크린샷도 같은 폴더에 `절번호-설명.png`로 남긴다.
   **시험이 정상 용량을 못 내는 경우** (모두 기록하고 멈추거나 재시도한다. 고치려 하지 말고, 이유를 재지 않았으면 "원인 미확인"):

   | 증상 | 의미와 할 일 |
   |---|---|
   | 시작이 409 "이미 부하 시험이 실행 중입니다" | 이전 시험이 끝나기를 기다린다(앱에서 진행 단계 확인). 서버를 재시작했다면 기록 파일의 `phase`를 보고 끝까지 안 갔으면 다시 시작한다. |
   | 기록의 `error`가 있음("guidellm이 실패했습니다 (종료 코드 N)", "제한 시간 안에 끝나지 않았습니다", "키를 폐기하지 못했습니다") | 그 문구를 그대로 적는다. 게이트웨이에 직접 요청이 가는지(`curl`), `--gateway-url`이 맞는지, 시험 테넌트 키가 폐기됐는지(`ttalkkak ops tenants list`)를 확인하고 원인을 쟀으면 적는다. 못 쟀으면 "원인 미확인". 용량 기록에서 뺀다. |
   | `no_capacity_reason`이 있고 `capacity_tpm`이 없음 (`indeterminate_at_minimum`, `objectives_unmet_at_minimum`, 해결된 통과 수준 없음) | 용량을 정하지 않은 시험이다. 1명에서도 목표 미달이면 게이트웨이 경유 첫 글자 시간을 직접 재 보고(`curl -w`), 요청 수가 모자란 판정 불가이면 위 74개 항목대로 단계 시간 문제로 적는다. 원인을 재지 않았으면 "원인 미확인". |
   | 모든 확인 실패(`confirmed` false) | 아래 4번. |
   | 게이트웨이/Prometheus에 못 닿음 (앱 상태 줄 `연결 끊김`, 실시간 화면 비어 있음) | 5절 포트 포워딩과 `--gateway-url`을 다시 확인하고, 고친 방법을 ledger에 적는다. |

4. **열린 부하 확인이 실패하면 이유를 잰다(짐작 금지).** 실패한 확인의 JSON(`confirms[]`)에서 달성률, 오류율, 요청 속도 대비 달성 속도(`achieved_rps`), 첫 글자 p99를 보고, 같은 속도로 더 길게(예: 120초) 포아송을 직접 돌려 본다.
   ```bash
   # 키는 부하 시험 전용 테넌트(ttalkkak-loadtest)로 발급해 파일로만 받는다. 화면에 찍지 않는다.
   umask 077
   .venv/bin/ttalkkak ops tenants key-issue ttalkkak-loadtest > /tmp/check.key
   .venv/bin/python - <<'PY'
   import subprocess
   from pathlib import Path
   from ttalkkak.cost.runner import poisson_cmd
   key = Path("/tmp/check.key").read_text().split()[-1]   # 출력 형식이 다르면 파일을 사람이 확인
   cmd = poisson_cmd("<게이트웨이 주소>/v1", "<모델>", "kind=synthetic_text,prompt_tokens=256,output_tokens=128",
                     Path("poisson-check.json"), rate=<요청/초>, ttft_ms=1000, itl_ms=None,
                     seconds=120, warmup_seconds=5, api_key=key)
   subprocess.run(cmd, check=True)   # 명령을 출력하지 않는다(키가 들어 있다)
   PY
   rm /tmp/check.key
   ```
   콘솔이 쓰는 인자와 같다(`guidellm run --help`로 확인됨). 시험이 끝나면 키를 폐기한다. 접두어는 `tk_ttalkkak-loadtest_` 뒤 8자까지이고(키 전체가 아니라 그 앞부분만 쓴다) `ttalkkak ops tenants key-revoke <접두어>`로 폐기한다. 결과 문서에 명령을 옮길 때 키 값이 들어 있지 않은지 확인한다.
5. 복제본 3개 용량이 1개보다 작거나 비슷하면 2절 spike 결과와 맞는지만 비교해서 적고, 이유는 재지 않았다면 "원인 미확인"으로 둔다.

## 8. 시험 중 복제본 하나 끄기

복제본 3개 상태에서 7절의 시험을 시작하고, 시험이 `용량 찾는 중` 단계에 있는 동안 앱의 복제본 표에서 파드 하나를 골라 `끄기`(또는 `kubectl delete pod <파드>`)를 누른다.
- 끄기 전후의 오류율, 첫 글자 p99, 새 복제본이 ready 되기까지 걸린 시간(`kubectl get pod -w`의 시각), 시험이 끝까지 돌았는지를 기록한다.
- 끄고 돌린 시험도 자동 증감을 끈 채였다면, 확인을 통과했을 때 복제본 3개 용량 기록을 낮아진 값으로 덮어쓴다. 결과와 상관없이(판정 불가/확인 실패 포함) 이 시험은 용량 기록 후보에서 뺀다고 ledger에 적고, **반드시** 파드를 끄지 않은 상태로 같은 시험을 한 번 더 돌려 정상 기록을 복구한다. 두 시험의 `id`를 구분해 결과 문서에 적는다.
- 결과: `docs/results/project2/kill-replica.md`.

## 9. 자동 증감을 켜고 1 → 20 → 1

전제: 최대 복제본 수까지 모든 수의 용량 기록이 있다(7절을 수마다 한 번). 앱 실시간 탭에 "부하 시험 필요"가 없어야 한다. 없으면 먼저 그 수의 시험을 돈다.

1. 자동 증감 스위치를 켠다(`● 자동 증감 켜짐`). 복제본을 1개로 맞추고(`kubectl scale ... --replicas=1`) ready를 기다린다. 이 상태를 시작 상태로 기록한다.
2. 부하를 올린다. 결정적인 방법은 부하 시험 카드에서 `자동 증감 켠 채로 시험`을 체크하고 `최대 사용자` 20으로 시작하는 것이다(이 결과는 용량 기록에 저장되지 않는다). 시험은 약 8분이다. 탐색이 낮은 단계에서 멈추거나 끝나 버리면 부하가 20명으로 2분 넘게 이어지지 않아 증가 규칙(용량 사용률 ≥ 80 %가 2분 넘게, 또는 대기 요청 > 0이 2분 넘게)이 안 걸릴 수 있다. 이 시험은 용량을 찾는 시험이라서 그 사이에 단계 부하와 증가 판정이 섞여 있으니, 각 결정이 어느 시험 단계 중에 났는지 시각으로 맞춰 적는다.
   - 시험 후 복제본이 안 늘었으면 같은 시험을 한 번 더 돌린다(부하 시험 두 번 + 대기가 계획). 두 번째 이후에도 안 늘면, 20명을 고정으로 몇 분 유지하는 부하를 직접 건다(위 7절 4번과 같은 방식으로 키를 파일로 받아 `guidellm run --profile kind=concurrent --override profile.streams 20 --constraint kind=max_duration,seconds=300 ...`을 `concurrent` 프로필로 실행. 인자는 `guidellm run --help`와 `src/ttalkkak/cost/runner.py`의 `_tail`/`_backend`와 같게 맞춘다).
   - 그래도 결정이 없으면 멈춘다. 실시간 탭의 용량 사용률, 대기 요청, 현재 복제본의 용량 기록 유무("부하 시험 필요"가 뜨는지)를 값 그대로 적고, 원인을 재지 못했으면 "원인 미확인"이라고 쓴다.
3. 시험이 끝난 뒤 대기: 감소 규칙은 둘이다(스펙 4.6). ① 용량 사용률 ≤ 30 %가 10분 넘게, 대기 0 ② 5분 넘게 처리 0인 복제본이 있고 남은 복제본으로 사용률이 60 % 이하로 예상됨. 둘 중 하나가 걸려 1개로 돌아올 때까지 최소 10분 이상 둔다. 어느 규칙으로 줄었는지 결정 기록의 이유로 적는다. 늘린 뒤 5분, 줄인 뒤 10분은 다시 바꾸지 않는 것이 정상이다.
4. 기록: 결정마다 시각, 이유, 조치(from → to 복제본), 새 복제본 준비 시간, 그동안의 첫 글자 p99와 오류율을 `docs/results/project2/autoscale.md`에 적는다. 원본은 `runs/autoscale/<날짜>.jsonl`과 실시간 탭의 결정 목록이다.

## 10. 맥 앱 GUI 확인 (사람이 맥에서, 데스크탑은 5절의 콘솔을 켜 둔 채)

맥 세션에서는 화면 접근 권한이 없어 아래를 하지 못했다. 사람이 앱에서 직접 하고 체크한다. 서버 없이 보려면 맥에서 `scripts/fake_console.py`(`.venv/bin/python scripts/fake_console.py <작업폴더> --port 8765`)로 가짜 서버를 띄워 시나리오를 바꿀 수 있다(`control.json`).

- [ ] **Cmd+V 토큰 붙여넣기**: 연결 화면의 운영자 토큰 칸(`ttk_op_...`)에 Cmd+V로 붙여넣어진다. 잘 안 되면 앱 메뉴의 `편집 → 붙여넣기`도 시도하고 결과를 적는다.
- [ ] **평문 동의 체크박스**: 서버 주소를 `http://<일반 주소>`(예: `http://192.168.0.5:8787`)로 바꾸면 "위험을 이해했고, 이 주소로 토큰을 보냅니다" 체크박스가 나타나고 체크 전에는 연결이 거절된다. 체크한 뒤 주소를 바꾸면 체크가 풀린다(재설정).
- [ ] **tailnet 100.x 주소는 경고가 없다**: `http://100.x.x.x:8787`(Tailscale 100.64.0.0/10)과 `*.ts.net`을 넣어도 체크박스가 나타나지 않는다.
- [ ] **키체인**: 연결에 성공하면 macOS 키체인 접근 허용 창이 뜨고 토큰이 저장된다(키체인 접근 앱에서 항목 확인). 앱을 껐다 켜면 저장된 연결로 바로 붙는다. 메뉴 `연결 → 연결 끊기`를 누르면 저장된 토큰이 지워지고 연결 화면으로 돌아간다(키체인 항목이 사라지는지 확인). 키체인을 못 쓰는 경우 "토큰을 설정 파일(권한 0600)에 저장했습니다" 경고가 뜨는지.
- [ ] **연결 직후 e2e ping**: 연결에 성공하면 상태 줄에 칩 배지와 `연결됨`이 보인다(`/api/ping` 결과). 서버의 `ttalkkak-autoscale` ConfigMap을 읽지 못하면 "ttalkkak-autoscale 설정을 읽지 못해…" 오류가 보이는지(가짜 서버가 아니라 실제 서버에서 평소에는 나오지 않아야 한다).
- [ ] **탭이 모두 그려진다**: 실시간, 개요, 원가, 테넌트, 사용량, 서빙 실행 여섯 개를 차례로 연다. 각각 스크린샷.
- [ ] **실시간 화면 세 상태**
  - 정상: 용량 사용률 원형 그래프, 복제본 표, 최근 5분 그래프, 결정 목록이 2초마다 갱신된다.
  - `needs_loadtest`: **실제 클러스터의 `ttalkkak-capacity`를 지우지 않는다**(7절의 복제본별 기록이 사라진다). 맥에서 `scripts/fake_console.py` 작업폴더의 `control.json`에 `{"capacity": false}`를 쓰고 앱을 가짜 서버(`--port 8765`)에 연결해 본다. 확인 실패 화면은 `"capacity": "unconfirmed"`, 새 시험이 확인에 실패한 화면은 `"attempt"`. 이 상태에서 "부하 시험을 먼저 돌리세요"와 `부하 시험으로 이동` 버튼이 보이고 버튼이 부하 시험 카드로 간다. 확인 실패 기록이면 "열린 부하(포아송) 확인에서 목표를 지키지 못한 기록이라 쓰지 않습니다"가 보인다.
  - 서버 꺼짐: 5절 콘솔을 중단하면 상태 줄이 `연결 끊김`으로 바뀌고 다시 켜면 `연결됨`으로 돌아온다.
- [ ] **CSV 내보내기**: 사용량 탭의 CSV 버튼을 누르면 `~/Downloads/invoice.csv`가 생기고(있으면 `invoice-1.csv`처럼 덮어쓰지 않음) 앱에 "CSV를 저장했습니다: <경로>" 토스트가 뜬다. 파일을 열어 본다.
- [ ] **API 키 모달은 Esc를 무시한다**: 테넌트 탭에서 `키 발급`→확인 후 나오는 "새 API 키" 창이 Esc로 닫히지 않고, 닫기 버튼으로만 닫힌다.
- [ ] **OS 알림**: 알림 권한 요청 창이 뜨고 허용하면 `알림` 체크가 켜진다(거부하면 "알림 — 권한 없음" 문구). 9절 자동 증감 중 복제본이 늘 때와 줄 때 각각 macOS 알림이 온다. `알림` 체크를 끄면 알림이 오지 않는다.
- [ ] **부하 시험 카드**: 시험 중 진행 단계가 갱신되고, 끝나면 탐색 단계 표, 확인 결과, 무릎, 동시 사용자 대 첫 글자 p99 그래프가 보인다. 7절 숫자와 화면이 같다.
- [ ] **스크린샷 저장**: 위 화면을 `docs/results/project2/app/` 아래에 파일 이름 `01-connect.png`, `02-live-normal.png`, `03-live-needs-loadtest.png`, `04-live-disconnected.png`, `05-loadtest-result.png`처럼 저장하고 커밋한다.

각 항목의 통과/실패와 실패 시 증상은 `docs/results/project2/app-gui-check.md`에 적는다. 앱 버그가 나오면 증상만 적고 고치지 않는다(맥 세션이 고친다).

## 11. 결과 정리와 기준 표

`docs/results/project2/summary.md`에 아래 표를 채운다. 스펙 5절의 기준은 모두 일곱 줄이다(여섯 줄은 데스크탑에서 재는 것, 일곱 번째는 코드로 확인되는 것). 모두 근거 파일이 있어야 프로젝트 2가 끝난다.

| 기준 | 결과 | 근거 파일 |
|---|---|---|
| 복제본 여러 개와 라우팅이 데스크탑에서 실제로 돈다 | | |
| 콘솔 "실시간" 탭에 실제 지표가 2초마다 갱신된다 | | |
| 콘솔에서 부하 시험을 돌려 복제본 1개와 3개의 결과를 한 그래프에 겹쳐 볼 수 있고, 그 숫자가 결과 문서로 남는다 | | |
| 복제본 하나를 꺼도 서비스가 이어지는지 실측 결과가 있다 | | |
| 부하를 올렸다 내렸을 때 복제본이 자동으로 늘고 주는 실측 기록(시각, 이유, 준비 시간, 그동안의 첫 글자 p99)이 있다 | | |
| 데스크탑 앱 설치 파일이 macOS·Windows·Linux로 빌드되고, 맥 앱이 데스크탑의 콘솔 서버에 토큰으로 붙어 실시간 화면이 돈다 | | |
| NVIDIA·AMD의 박힌 값이 설정 파일로 옮겨졌고 기존 테스트가 모두 통과한다. 견본 칩 파일이 형식 검사를 통과한다 | `.venv/bin/python -m pytest -q` 결과 | 테스트 출력 파일 |

결과 표를 채울 때: 계획(이 문서)과 다르게 한 결정은 모두 `docs/project2-ledger.md`에 `R1`부터 적는다.
