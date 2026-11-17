# 프로젝트 2 데스크탑 실기 검증 지시문 (몰려도 버티기)

**대상:** Windows 10 + WSL2 + Docker Desktop + k3s, RTX 3060 Ti 8GB, Tailscale 호스트 이름 `desk`. 데스크탑의 Claude가 이 문서를 위에서 아래로 따라 하고, 맥 앱을 쓰는 확인은 사람이 맥에서 한다.

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
  - 기록: `runs/loadtests/<시각>.json`(필드 `stages`, `stop_reason`, `confirms`, `capacity_streams`, `capacity_tpm`, `knee`, `no_capacity_reason`)과 ConfigMap `ttalkkak-capacity`(복제본 수마다 하나, 자동 증감을 끈 시험만 저장). 부하 시험 키는 끝나면 폐기되고, 사용량은 청구서에서 빠진다.
- **용량 기록은 복제본 수마다 따로**: 자동 증감은 현재 복제본 수의 기록만 쓴다. 기록이 없는 수는 콘솔에 "부하 시험 필요: 2개, 3개"로 보이고 자동 증감은 거기서 멈춘다. 추정값은 쓰지 않는다. 그래서 최대 복제본 수까지 각각 한 번씩(자동 증감 끈 채) 시험해야 한다.
- **`ttalkkak-ops` Deployment는 Recreate 전략**이다. 롤아웃 때 몇 초 동안 ops API가 끊기는 것이 정상이다(자동 증감 루프가 두 개 겹치지 않게 하려는 것).
- **수동 복제본 수 조절은 앱에서 뺐다.** 복제본 수는 자동 증감이 바꾸거나, 아래 `kubectl scale`로 직접 바꾼다. 복제본 끄기(파드 삭제)는 앱의 복제본 표에서 한다.

## 1. 브랜치와 환경

```bash
git fetch origin && git checkout project2 && git pull --ff-only
uv sync --extra ops --extra measure   # 이미 했으면 건너뜀
.venv/bin/ttalkkak ops --help
```

## 2. spike 결과 확인

`docs/results/spike-replicas/`를 연다(`docs/desktop/spike-replicas.md`를 이미 돌렸다면). 없으면 그 문서를 먼저 따라 하거나, 건너뛰는 결정을 ledger에 적는다. 복제본 1개와 3개의 TTFT p99, 출력 토큰/초, KV 캐시 크기를 `docs/results/project2/summary.md`에 옮겨 적는다. 스펙 2절 표의 결론(3개가 1개보다 빠른가)은 이 숫자로만 쓴다.

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

적용 뒤 확인:

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}'   # nvidia.com/gpu가 3
```

방법 B (A가 k3s에서 안 될 때, `--gpu-share runtime`): 파드가 `nvidia.com/gpu`를 요청하지 않고 `runtimeClassName: nvidia`와 `NVIDIA_VISIBLE_DEVICES`로 GPU를 직접 본다. `nvidia.com/gpu` 칩에서만 된다. 어느 방법을 썼는지, A가 안 됐다면 오류 메시지를 `docs/results/project2/summary.md`와 ledger에 적는다.

## 4. 게이트웨이 배포와 한 GPU에 몇 개까지 뜨는지

```bash
.venv/bin/ttalkkak ops export --run <run> --target gateway --out deploy/ \
  --max-replicas 3 --autoscale --gpu-share resource --hostname <호스트>
.venv/bin/ttalkkak ops cluster up --out deploy/
.venv/bin/ttalkkak ops cluster status
```

- `--max-replicas`는 GPU 메모리 비율을 나누는 기준이다. 자동 증감을 켜면 최대 기준으로 비율을 고정한다(예: 3개면 복제본마다 `--gpu-memory-utilization` 약 0.28). 복제본이 1개일 때도 이 비율을 쓴다.
- `--max-replicas`를 2, 3, 4로 올려 보며(같은 명령을 `--force`로 다시) 몇 개까지 모두 `Running/Ready`가 되는지, 못 뜨면 어떤 오류로 못 뜨는지 기록한다. 결과 표: 복제본 수 | 복제본당 메모리 비율 | 모두 ready? | 시작 로그의 `GPU KV cache size` | `nvidia-smi` 사용 메모리.
- 가장 큰 성공 수를 이후 단계의 최대 복제본 수로 정한다. 3이 아니면 이후 문서의 3을 그 수로 바꿔 읽고 ledger에 적는다.

## 5. 콘솔 서버(원격)와 맥 앱 연결

데스크탑(콘솔 서버 쪽):

```bash
kubectl port-forward -n ttalkkak svc/prometheus 9090:9090 &
kubectl get svc -A | grep -i envoy        # 게이트웨이 서비스 주소 확인
.venv/bin/ttalkkak ops token new          # 운영자 토큰을 한 번 출력
.venv/bin/ttalkkak ops ui --remote --prometheus-url http://127.0.0.1:9090 \
  --gateway-url http://<게이트웨이 주소>/v1
```

- `--remote`는 `tailscale ip -4`가 알려 주는 tailnet 주소(100.x.x.x)에만 연다. tailscale이 꺼져 있으면 시작을 거부하고 `--host`를 직접 주라고 한다. `0.0.0.0`은 운영자가 `--host 0.0.0.0`으로 직접 줄 때만 열린다(LAN 노출이라 권하지 않음).
- 시작할 때 "tailnet 안에서도 통신은 평문 HTTP이며 토큰이 요청마다 전송됩니다"라는 경고가 stderr로 나온다. 정상이다. 토큰은 `~/.ttalkkak/console-token`에 있고 `ttalkkak ops token show`로 다시 볼 수 있으며 `ttalkkak ops token revoke`로 폐기한다(떠 있는 서버는 즉시 거절).
- 기본 포트 8787, 주소는 `http://<tailnet 100.x 주소>:8787`.

맥(앱 설치): 저장소 GitHub Actions의 `desktop` 워크플로 실행에서 Artifacts의 macOS용 `.dmg`(Apple Silicon 또는 Intel)를 받는다. 서명하지 않은 빌드다.
1. `.dmg`를 열어 앱을 Applications로 옮긴다.
2. 앱을 우클릭하고 `열기`를 누른다. 막히면 시스템 설정 → 개인정보 보호 및 보안에서 `그래도 열기`를 누른다.
3. 서버 주소에 위 tailnet 주소, 운영자 토큰에 `ops token new`가 출력한 값을 넣고 `연결`.

## 6. 지표 이름 대조와 게이트웨이 접근 로그

1. 질의 상수의 지표 이름을 실제 `/metrics`와 맞춰 본다.
   ```bash
   kubectl port-forward -n ttalkkak <vllm 파드> 8000:8000 &
   curl -s localhost:8000/metrics | grep -E "^vllm:" | cut -d'{' -f1 | sort -u
   ```
   `src/ttalkkak/live/prom.py`의 질의 상수와 하나씩 비교한다. 다르면 코드를 고치고(테스트도) 별도 커밋으로 남기고 ledger에 적는다.
2. 게이트웨이 429와 고객별 토큰을 접근 로그에서 읽는 것이 맞는지: 한도를 낮춰 429를 낸 뒤(`ttalkkak ops tenants set-limit <id> <값>`) 콘솔의 거절 수와 고객별 한도 소진률이 접근 로그(`kubectl logs`로 직접 확인)와 같은지 비교해 결과 표에 적는다.

## 7. 부하 시험 — 자동 증감을 끄고 복제본 1개 vs 3개

시작 전: 앱 실시간 탭에서 `자동 증감` 스위치를 끈다(`● 자동 증감 켜짐` → `– 자동 증감 꺼짐`, 확인 창에서 `끄기`). 부하 시험 카드의 `자동 증감 켠 채로 시험`은 체크하지 않는다(체크하면 결과가 용량 기록에 저장되지 않는다). `최대 사용자`는 기본 20.

복제본 수를 직접 맞춘다.

```bash
DEP=$(kubectl get configmap ttalkkak-autoscale -n ttalkkak -o jsonpath='{.data.deployment}')
kubectl scale deployment/$DEP -n ttalkkak --replicas=1
kubectl rollout status deployment/$DEP -n ttalkkak
```

1. 복제본 1개: `시작`(확인 창에서 `시작`). 시험 중 앱에 진행 단계(`용량 찾는 중` → `열린 부하로 확인 중`)가 보인다. 끝나면 `runs/loadtests/<id>.json`과 ConfigMap `ttalkkak-capacity`의 `1` 항목을 확인한다. 약 8분 이하.
2. `--replicas=3`(또는 4절에서 정한 최대 수)으로 바꾸고 `rollout status`가 끝나 전부 ready가 된 뒤 같은 시험을 한 번 더 한다.
3. 결과를 `docs/results/project2/loadtest-1vs3.md`에 표로 남긴다. **시험마다 반드시 기록할 것:**
   - 탐색 단계 표(앱의 시험 상세 표와 같은 열): 순서 | 동시 사용자 | 달성률 | 신뢰 구간 | 판정(통과/실패/모름) | 오류율 | 중단 | 토큰/분
   - 중단 사유(`stop_reason`: `converged`, `max_streams_reached`, `objectives_unmet_at_minimum`, `indeterminate_at_minimum`, `max_probes_exhausted`, `constraint_stopped_escalation`)와 앱이 보여 주는 한국어 문구
   - 열린 부하 확인 결과: 확인 번호별 `확인 통과/확인 실패`, 수준(동시 사용자), 요청 속도, 달성률과 신뢰 구간, 오류율, 첫 글자 p99, 토큰/분. 최종 `confirmed`가 true인지 false인지와 **확인에서 달성한 토큰/분**(`capacity_tpm`)
   - 무릎: 동시 사용자 수와 출력 토큰/초(없으면 이유)
   - 동시 사용자 수별 첫 글자 p99 곡선(앱의 겹쳐 그린 그래프, 1개와 3개 한 그래프)을 스크린샷으로
4. **열린 부하 확인이 실패하면 이유를 잰다(짐작 금지).** 실패한 확인의 JSON(`confirms[]`)에서 달성률, 오류율, 요청 속도 대비 달성 속도(`achieved_rps`), 첫 글자 p99를 보고, 같은 속도로 더 길게(예: 120초) 포아송을 직접 돌려 본다.
   ```bash
   .venv/bin/python - <<'PY'
   from pathlib import Path
   from ttalkkak.cost.runner import poisson_cmd
   cmd = poisson_cmd("<게이트웨이 주소>/v1", "<모델>", "kind=synthetic_text,prompt_tokens=256,output_tokens=128",
                     Path("poisson-check.json"), rate=<요청/초>, ttft_ms=1000, itl_ms=None,
                     seconds=120, warmup_seconds=5, api_key="<새로 발급한 키>")
   print(" ".join(cmd))
   PY
   ```
   출력된 `guidellm run ...` 명령을 그대로 실행한다(콘솔이 쓰는 인자와 같다. `guidellm run --help`로 확인됨). 키는 `ttalkkak ops tenants key-issue <테넌트>`로 새로 발급하고 끝나면 `ttalkkak ops tenants key-revoke <키 접두어>`로 폐기한다.
5. 복제본 3개 용량이 1개보다 작거나 비슷하면 2절 spike 결과와 맞는지만 비교해서 적고, 이유는 재지 않았다면 "원인 미확인"으로 둔다.

## 8. 시험 중 복제본 하나 끄기

복제본 3개 상태에서 7절의 시험을 시작하고, 시험이 `용량 찾는 중` 단계에 있는 동안 앱의 복제본 표에서 파드 하나를 골라 `끄기`(또는 `kubectl delete pod <파드>`)를 누른다.
- 끄기 전후의 오류율, 첫 글자 p99, 새 복제본이 ready 되기까지 걸린 시간(`kubectl get pod -w`의 시각), 시험이 끝까지 돌았는지를 기록한다.
- 시험이 이 때문에 판정 불가/확인 실패가 되면 그 사실을 그대로 적는다(이 시험 결과는 용량 기록 후보에서 뺀다. ledger에 적는다). 다시 같은 시험을 끈 채로 한 번 더 돌려 정상 기록을 남긴다.
- 결과: `docs/results/project2/kill-replica.md`.

## 9. 자동 증감을 켜고 1 → 20 → 1

전제: 최대 복제본 수까지 모든 수의 용량 기록이 있다(7절을 수마다 한 번). 앱 실시간 탭에 "부하 시험 필요"가 없어야 한다. 없으면 먼저 그 수의 시험을 돈다.

1. 자동 증감 스위치를 켠다(`● 자동 증감 켜짐`). 복제본 1개에서 시작한다.
2. 부하를 올린다: 부하 시험 카드에서 `자동 증감 켠 채로 시험`을 체크하고 `최대 사용자` 20으로 시작한다(체크하면 이 시험 결과는 용량 기록에 저장되지 않는다). 시험(최대 약 8분)이 끝나도 부하가 남지 않으므로, 증가 규칙(용량 사용률 ≥ 80 %가 2분 넘게, 또는 대기 요청 > 0이 2분 넘게)이 걸리는지 실시간 탭의 결정 목록으로 본다. 한 번에 안 오르면 시험을 다시 돌려 더 오래 부하를 준다. 시간이 모자라면 방법을 ledger에 적는다.
3. 시험이 끝난 뒤 대기: 감소 규칙(용량 사용률 ≤ 30 %가 10분 넘게, 대기 0)이 걸려 1개로 돌아올 때까지 최소 10분 이상 둔다. 늘린 뒤 5분, 줄인 뒤 10분은 다시 바꾸지 않는 것이 정상이다.
4. 기록: 결정마다 시각, 이유, 조치(from → to 복제본), 새 복제본 준비 시간, 그동안의 첫 글자 p99와 오류율을 `docs/results/project2/autoscale.md`에 적는다. 원본은 `runs/autoscale/<날짜>.jsonl`과 실시간 탭의 결정 목록이다.
5. 결정이 안 나면(예: 용량 사용률이 안 오름) 지표 값을 그대로 적고 원인은 재서 적는다. 못 쟀으면 "원인 미확인".

## 10. 맥 앱 GUI 확인 (사람이 맥에서, 데스크탑은 5절의 콘솔을 켜 둔 채)

맥 세션에서는 화면 접근 권한이 없어 아래를 하지 못했다. 사람이 앱에서 직접 하고 체크한다. 서버 없이 보려면 맥에서 `scripts/fake_console.py`(`.venv/bin/python scripts/fake_console.py <작업폴더> --port 8765`)로 가짜 서버를 띄워 시나리오를 바꿀 수 있다(`control.json`).

- [ ] **Cmd+V 토큰 붙여넣기**: 연결 화면의 운영자 토큰 칸(`ttk_op_...`)에 Cmd+V로 붙여넣어진다. 잘 안 되면 앱 메뉴의 `편집 → 붙여넣기`도 시도하고 결과를 적는다.
- [ ] **평문 동의 체크박스**: 서버 주소를 `http://<일반 주소>`(예: `http://192.168.0.5:8787`)로 바꾸면 "위험을 이해했고, 이 주소로 토큰을 보냅니다" 체크박스가 나타나고 체크 전에는 연결이 거절된다. 체크한 뒤 주소를 바꾸면 체크가 풀린다(재설정).
- [ ] **tailnet 100.x 주소는 경고가 없다**: `http://100.x.x.x:8787`(Tailscale 100.64.0.0/10)과 `*.ts.net`을 넣어도 체크박스가 나타나지 않는다.
- [ ] **키체인**: 연결에 성공하면 macOS 키체인 접근 허용 창이 뜨고 토큰이 저장된다(키체인 접근 앱에서 항목 확인). 앱을 껐다 켜면 저장된 연결로 바로 붙는다. 메뉴 `연결 → 연결 끊기`를 누르면 저장된 토큰이 지워지고 연결 화면으로 돌아간다(키체인 항목이 사라지는지 확인). 키체인을 못 쓰는 경우 "토큰을 설정 파일(권한 0600)에 저장했습니다" 경고가 뜨는지.
- [ ] **탭이 모두 그려진다**: 실시간, 개요, 원가, 테넌트, 사용량, 서빙 실행 여섯 개를 차례로 연다. 각각 스크린샷.
- [ ] **실시간 화면 세 상태**
  - 정상: 용량 사용률 원형 그래프, 복제본 표, 최근 5분 그래프, 결정 목록이 2초마다 갱신된다.
  - `needs_loadtest`: 용량 기록이 없는 상태(`kubectl delete configmap ttalkkak-capacity -n ttalkkak` 뒤 `kubectl apply`로 `{}` 복구하거나 fake_console `capacity: false`)에서 "부하 시험을 먼저 돌리세요"와 `부하 시험으로 이동` 버튼이 보이고 버튼이 부하 시험 카드로 간다. 확인 실패 기록이면 "열린 부하(포아송) 확인에서 목표를 지키지 못한 기록이라 쓰지 않습니다"가 보인다.
  - 서버 꺼짐: 5절 콘솔을 중단하면 상태 줄이 `연결 끊김`으로 바뀌고 다시 켜면 `연결됨`으로 돌아온다.
- [ ] **CSV 내보내기**: 사용량 탭의 CSV 버튼을 누르면 `~/Downloads/invoice.csv`가 생기고(있으면 `invoice-1.csv`처럼 덮어쓰지 않음) 앱에 "CSV를 저장했습니다: <경로>" 토스트가 뜬다. 파일을 열어 본다.
- [ ] **API 키 모달은 Esc를 무시한다**: 테넌트 탭에서 `키 발급`→확인 후 나오는 "새 API 키" 창이 Esc로 닫히지 않고, 닫기 버튼으로만 닫힌다.
- [ ] **OS 알림**: 알림 권한 요청 창이 뜨고 허용하면 `알림` 체크가 켜진다(거부하면 "알림 — 권한 없음" 문구). 9절 자동 증감 중 복제본이 늘 때와 줄 때 각각 macOS 알림이 온다. `알림` 체크를 끄면 알림이 오지 않는다.
- [ ] **부하 시험 카드**: 시험 중 진행 단계가 갱신되고, 끝나면 탐색 단계 표, 확인 결과, 무릎, 동시 사용자 대 첫 글자 p99 그래프가 보인다. 7절 숫자와 화면이 같다.
- [ ] **스크린샷 저장**: 위 화면을 `docs/results/project2/app/` 아래에 파일 이름 `01-connect.png`, `02-live-normal.png`, `03-live-needs-loadtest.png`, `04-live-disconnected.png`, `05-loadtest-result.png`처럼 저장하고 커밋한다.

각 항목의 통과/실패와 실패 시 증상은 `docs/results/project2/app-gui-check.md`에 적는다. 앱 버그가 나오면 증상만 적고 고치지 않는다(맥 세션이 고친다).


## 11. 결과 정리와 기준 표

`docs/results/project2/summary.md`에 아래 표를 채운다. 스펙 5절의 기준 여섯 개 모두 근거 파일이 있어야 프로젝트 2가 끝난다.

| 기준 | 결과 | 근거 파일 |
|---|---|---|
| 복제본 여러 개와 라우팅이 데스크탑에서 실제로 돈다 | | |
| 콘솔 "실시간" 탭에 실제 지표가 2초마다 갱신된다 | | |
| 콘솔에서 부하 시험을 돌려 복제본 1개와 3개의 결과를 한 그래프에 겹쳐 볼 수 있고, 그 숫자가 결과 문서로 남는다 | | |
| 복제본 하나를 꺼도 서비스가 이어지는지 실측 결과가 있다 | | |
| 부하를 올렸다 내렸을 때 복제본이 자동으로 늘고 주는 실측 기록(시각, 이유, 준비 시간, 그동안의 첫 글자 p99)이 있다 | | |
| 데스크탑 앱 설치 파일이 macOS·Windows·Linux로 빌드되고, 맥 앱이 데스크탑의 콘솔 서버에 토큰으로 붙어 실시간 화면이 돈다 | | |

스펙 5절에는 이 여섯 줄 뒤에 일곱 번째 줄이 하나 더 있다. 코드로 확인되는 항목이라 같은 표에 둔다.

| 칩 설정 파일: NVIDIA·AMD의 박힌 값이 설정 파일로 옮겨졌고 기존 테스트가 모두 통과한다. 견본 칩 파일이 형식 검사를 통과한다 | `.venv/bin/python -m pytest -q` 결과를 적는다 | 테스트 출력 |

결과 표를 채울 때: 계획(이 문서)과 다르게 한 결정은 모두 `docs/project2-ledger.md`에 `R1`부터 적는다.
