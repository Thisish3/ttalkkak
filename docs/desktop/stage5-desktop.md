# Stage 5 데스크탑 검증 지시 (Task 12, 13)

**전제(2026-11-12 갱신):** hxccl은 v0.2로 main에 merge됐다. 이 작업을 바로 시작한다.

**먼저 읽을 것:** `docs/specs/2026-11-12-ttalkkak-product-definition.md`. ttalkkak의 정의가 바뀌었다. 이 검증은 그 문서의 "프로젝트 1: 뼈대 실증"이다. 기준은 하나다: **"된다"고 적는 것은 실제로 돌려 보고 출력이나 숫자를 남긴 것만.** 안 되는 것은 숨기지 말고 "안 됨"과 이유(측정한 만큼)를 적는다.

## 0. 준비

```bash
cd ~/ttalkkak && git fetch && git switch stage5 && git pull
uv sync --all-extras && .venv/bin/python -m pytest -q tests   # 295 passed
```

반드시 읽을 것:
- `docs/plans/2026-11-11-ttalkkak-stage5.md`의 Task 12, 13
- `docs/stage5-ledger.md` (R1–R4 결정)
- `docs/specs/2026-11-11-ttalkkak-stage5-design.md` 10절

맥에서 구현과 리뷰(중간 1회, 최종 1회)는 끝났다. 데스크탑에서 할 일은 실제 클러스터에서 확인하고, 실제와 다른 부분을 고치는 것이다.

## 0.5 도구 준비

- `kubectl`이 PATH에 없다. ttalkkak의 `ops cluster`와 `ops bill --from-cluster`는 `kubectl` 명령을 직접 부른다. k3s 내장 kubectl을 연결한다: `sudo ln -s /usr/local/bin/k3s /usr/local/bin/kubectl`. `~/.kube/config`는 이미 있다.
- `helm`이 없다. 사전 설치(Envoy Gateway, AI Gateway) 전에 설치한다.
- 둘 다 ledger에 R번호로 적는다.

## 1. 규칙

- 측정하거나 확인하지 않은 원인은 "원인 미확인"으로 쓴다. 짐작으로 설명하지 않는다.
- 계획과 다르게 한 결정은 `docs/stage5-ledger.md`에 R5부터 번호를 붙여 적는다.
- 매니페스트를 고칠 때는 해당 테스트(`tests/test_gateway_manifests.py`, `tests/test_observability.py`)를 먼저 고쳐서 실패를 확인한 뒤 코드를 고친다.
- 커밋 author는 `Thisish3 <104387634+Thisish3@users.noreply.github.com>`이다. 메시지 끝에 `Co-Authored-By` 줄을 넣는다.
- push는 `stage5` 브랜치에만 한다. main merge는 하지 않는다.
- 메모리가 7 GB를 넘으면 진행하지 말고 멈춘 뒤, 무엇을 줄일지 보고한다.

## 2. Task 12: 실제 구성요소 확인

결과는 스펙의 "환경 기록"과 ledger에 적는다.

### (a) k3s와 GPU

```bash
kubectl get nodes
kubectl describe node | grep nvidia.com/gpu
```

### (b) 사전 설치

`ttalkkak ops export --target gateway`로 만든 README의 순서를 따른다. 버전은 `src/ttalkkak/gateway/versions.py`에 있다.

1. GIE v1.5.0 CRD
2. Envoy Gateway v1.10.0 (Redis rate limit 설정 포함)
3. AI Gateway v1.2.1

설치 후 확인: `kubectl get crd | grep -E "aigateway|inference|gateway"`

### (c) 리뷰어가 맥에서 확인하지 못한 항목

각각 실제로 확인하고 결과를 기록한다.

1. **Lua 필터와 AI Gateway ext_proc의 순서**: cache_salt를 넣는 Lua 필터가 ext_proc보다 먼저 도는지 확인한다.
   - 방법: acme 키로 같은 긴 프롬프트를 두 번 보내고, vLLM 로그나 지표에서 cache_salt가 적용됐는지 본다.
   - 순서가 맞지 않으면 R2의 대안(ops serve가 본문을 바꾸는 프록시)으로 간다.
2. **Dex 토큰 내용**: Dex v2.46.1 접근 토큰에 `groups`가 들어오는지, `aud`가 `ttalkkak-cli`인지 확인한다. 디바이스 로그인 후 토큰을 디코드해서 본다.
3. **Envoy 접근 로그**:
   - 값이 없을 때 JSON에 `"-"`로 나오는지 null로 나오는지
   - `x-request-id`를 클라이언트가 보낸 값 그대로 쓰는지
4. **EPP v1.5.0**:
   - Role에 `inferenceobjectives` 권한이 필요한지
   - 지표 엔드포인트에 인증이 필요한지
   - readiness gRPC 서비스 이름이 맞는지
   - EPP 파드 로그로 확인한다.
5. **HTTPRoute 이름**: AI Gateway가 HTTPRoute를 `ttalkkak`이라는 이름으로 만드는지 확인한다. SecurityPolicy와 EnvoyExtensionPolicy가 이 이름을 대상으로 한다.
   - `kubectl get httproute -n ttalkkak`
6. **CRD 필드**: `kubectl apply --dry-run=server -f deploy/`로 필드가 받아들여지는지 확인한다.
7. **DCGM exporter (WSL)**: 실행되는지, `DCGM_FI_DEV_POWER_USAGE`가 나오는지 확인한다. 안 되면 nvidia-smi를 읽는 작은 exporter로 대체하고 R로 기록한다.
8. **NetworkPolicy**: k3s 기본 flannel이 NetworkPolicy를 실제로 적용하는지 확인한다. Redis와 ops serve 접근 제한에 쓰인다. 적용이 안 되면 기록만 해 둔다.

### (d) vLLM 실제 지표 이름

```bash
curl :8000/metrics | grep -E "^vllm:" | cut -d'{' -f1 | sort -u
```

- `tests/test_observability.py`의 `KNOWN` 목록과 비교하고, 다르면 고친다.
- `--enable-prompt-tokens-details`를 켰을 때 `usage.prompt_tokens_details.cached_tokens`가 실제로 채워지는지 요청 한 번으로 확인한다.

### (e) 메모리

전체 구성요소를 띄운 뒤 `kubectl top pods -A`, `free -m`으로 잰다.

## 3. Task 13: 처음부터 끝까지

각 단계의 출력과 스크린샷을 `docs/results/stage5/`에 저장한다.

1. **원가 측정**
   ```bash
   ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct
   ttalkkak ops cost --run <run>
   ```
   확인: `cost.md`에 입력·캐시·출력 원가와 운영점이 나오는지, 전력 로그가 있는지.

2. **테넌트와 배포**
   ```bash
   ttalkkak ops tenants add acme --name Acme
   ttalkkak ops tenants add beta --name Beta
   ttalkkak ops tenants key-issue acme
   ttalkkak ops tenants user-add acme u1@acme
   docker build -t ttalkkak:local . && docker save ttalkkak:local | sudo k3s ctr images import -
   # TLS Secret(ttalkkak-tls, 자체 서명), grafana-admin, hf-token Secret 생성
   ttalkkak ops export --target gateway --run <run> --out deploy/ --hostname <호스트>
   ttalkkak ops cluster up --out deploy/
   ttalkkak ops cluster status
   ```
   확인: API 키로 `curl /v1/chat/completions`가 성공하는지.

   **2b. 사용자 로그인**: 다른 터미널에서 `ttalkkak login` → 브라우저 로그인 → `ttalkkak` 대화.
   확인: 턴마다 토큰과 예상 금액이 나오는지, `logout` 후 요청이 401인지.

3. **한도**: `ttalkkak ops tenants set-limit acme 200` 후 연속 요청을 보낸다. 429가 나와야 한다.

4. **캐시 분리**: 같은 긴 프롬프트를 acme 키와 beta 키로 각각 두 번 보낸다.
   - acme 두 번째 요청: `cached_tokens > 0`
   - beta 첫 요청: 0
   - vLLM 지표로도 확인한다.

5. **청구**
   ```bash
   ttalkkak ops bill --from-cluster --from <오늘> --to <내일> --cost runs/<run>/cost.json
   ```
   확인: 청구서 값이 손으로 계산한 값과 같은지.

6. **관측**: Grafana 대시보드 3개에 실시간 값이 나오는지, 경보 규칙이 로드됐는지. Jaeger에 게이트웨이 → vLLM 구간이 보이는지.

7. **운영 화면**: `ttalkkak ops ui` → 운영 탭 4개 스크린샷.

## 3.5 맥북 원격 경로 (사용자 결정: 정식 Tailscale)

시연은 맥북이 이용자 환경이고 데스크탑이 서비스다. 맥북에는 정식 Tailscale 앱을 설치한다(사용자가 설치). 데스크탑에서 다음을 준비하고 확인한다.

1. **이름과 인증서**
   - `tailscale status`로 이 기계의 MagicDNS 이름(`<기계>.<tailnet>.ts.net`)을 확인한다.
   - `sudo tailscale cert <그 이름>`으로 인증서를 받는다. 실패하면 tailnet 관리 화면에서 HTTPS 인증서 기능이 꺼져 있는 것이다. 사용자에게 켜 달라고 요청하고 기다린다.
   - 받은 인증서로 `ttalkkak-tls` Secret을 만든다(자체 서명 대신).
   - `ttalkkak ops export --target gateway ... --hostname <그 이름>`으로 다시 만든다. 로그인 서버 주소(issuer)도 이 이름을 쓴다.
2. **게이트웨이가 Tailscale 주소에서 열리는지**
   - k3s의 LoadBalancer(klipper)가 443을 어느 주소에 여는지 확인한다: `kubectl get svc -A | grep -i envoy`, `ss -ltnp | grep :443`.
   - 데스크탑 안에서 `curl -v https://<그 이름>/v1/pricing`이 인증서 경고 없이 되는지 본다.
   - WSL2의 네트워크 방식 때문에 Tailscale 주소로 안 열리면, 무엇이 막는지 측정한 만큼 적고 대안(`tailscale serve`로 443을 넘기기 등)을 시험한다. 결과를 R번호로 적는다.
3. **맥북에서 할 확인** (사용자와 맥 Claude가 한다. 데스크탑은 준비가 끝나면 주소를 보고한다)
   - `ttalkkak login --endpoint https://<그 이름>` → 브라우저 로그인 → `ttalkkak` 대화
   - 브라우저로 운영 콘솔 열기. `ops ui`는 지금 127.0.0.1에만 열린다. 원격에서 어떻게 열지(SSH 포워딩, 또는 게이트웨이 뒤에 두기)는 시험하고 결과를 적는다. 코드를 바꿔야 하면 맥에 보고하고 기다린다.

## 4. 끝나면

- README의 설치, 운영 흐름, 제약 절을 갱신하고 전체 테스트를 다시 돌린다.
- `stage5` 브랜치에 커밋하고 push한다.
- 맥에 보고할 내용:
  - 각 단계 통과/실패
  - 새로 정한 R번호들
  - 실측 메모리
  - 원인 미확인으로 남은 항목
