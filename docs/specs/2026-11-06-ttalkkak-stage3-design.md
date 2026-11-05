# ttalkkak 3단계 설계: Kubernetes 매니페스트 출력

- 작성일: 2026-11-06
- 상태: 사용자가 "알아서 진행"을 지시해 설계 결정을 위임함. 결정은 아래 2절에 근거와 함께 적는다.
- 선행: 1·2단계. 1·2단계 GPU 실측은 아직이다.

## 1. 목표

끝난 run(`runs/<id>/`)에서 **검증을 통과해 선택된 설정**을 그대로 Kubernetes 배포 매니페스트로 내보낸다. 로컬에서 검증한 결정이 클러스터 배포에서 다시 손으로 옮겨 적히며 틀어지는 일을 막는다.

```
ttalkkak export --run <id> --target k8s|kserve|llmd|dynamo --out deploy/
```

## 2. 결정 사항 (위임받아 결정)

| 항목 | 결정 | 이유 |
|---|---|---|
| 입력 | `pick.json`의 선택 후보 + `plan.json`의 인자 + `probe.json`의 vendor | 검증을 통과한 설정만 내보낸다 |
| 출력 대상 4개 | `k8s`(Deployment+Service), `kserve`(LLMInferenceService), `llmd`(kustomize overlay), `dynamo`(DynamoGraphDeployment) | 어느 클러스터에서나 되는 기본형 1개 + 1차 출처로 형식을 확인한 플랫폼 3개 |
| 인자 생성 | 2단계 엔진 어댑터의 `build_args`를 재사용하고, 대상별로 필요한 부분만 바꾼다 | 로컬 실행과 배포가 같은 변환표를 쓴다 |
| 검증 | 생성물을 YAML로 다시 읽어 구조를 단위 테스트한다. 실제 클러스터 적용은 사람이 하는 마지막 작업 | 개발 장비에 클러스터와 CRD가 없다 |
| 분산 구성 | 범위 밖(단일 GPU, replicas 1). AIConfigurator 연동은 하지 않는다 | 1·2단계가 단일 GPU 결정만 한다 |
| 라우터(llm-d EPP 등) | 매니페스트에 넣지 않고 README에 llm-d router 가이드 위치만 안내 | 라우터는 여러 인스턴스일 때 의미가 있고 별도 Helm values 체계다 |

## 3. 확인한 형식 (2026-11-06)

- **KServe** `docs/samples/llmisvc/agentic-tool-calling/llmisvc-single-gpu.yaml`: `apiVersion: serving.kserve.io/v1alpha2`, `kind: LLMInferenceService`, `spec.model.uri: hf://<id>`, `spec.model.name`, `spec.replicas`, `spec.router: {route: {}, scheduler: {}}`, `spec.template.containers[0]`(name `main`, image `ghcr.io/llm-d/llm-d-cuda:v0.9.0`, vLLM 추가 인자는 env `VLLM_ADDITIONAL_ARGS`, GPU는 `resources.limits/requests` `nvidia.com/gpu`). AMD 예시는 image `ghcr.io/llm-d/llm-d-rocm:v0.9.0`, `amd.com/gpu`.
- **llm-d** `guides/optimized-baseline/modelserver/gpu/vllm/base/`: kustomize. `resources`로 `guides/recipes/modelserver/base/single-host/default`(Deployment `decode`, 컨테이너 `modelserver`, 포트 8000), `components`로 `guides/recipes/modelserver/components/images/gpu-vllm/release`를 참조하고, `Deployment decode`의 `modelserver` 컨테이너를 patch(command `["vllm", "serve"]`, args에 모델과 엔진 인자, env `HF_TOKEN` from secret `llm-d-hf-token`, `nvidia.com/gpu`). SGLang patch는 command `["python3", "-m", "sglang.launch_server"]`, `--port=8000`. llm-d 최신 릴리스는 v1.0.0(2026-10-20).
- **Dynamo** `examples/backends/vllm/deploy/agg.yaml`: `apiVersion: nvidia.com/v1beta1`, `kind: DynamoGraphDeployment`, `spec.components`에 `type: frontend`와 `type: worker`. worker 컨테이너는 command `python3 -m dynamo.vllm`(SGLang은 `dynamo.sglang`), args에 엔진 인자, `envFrom: secretRef hf-token-secret`, `nvidia.com/gpu`. 파서는 엔진 인자가 아니라 Dynamo 런타임 인자 `--dyn-tool-call-parser`, `--dyn-reasoning-parser`(`components/src/dynamo/common/configuration/groups/runtime_args.py`). Dynamo 파서 이름 `hermes`, `qwen25`, `llama3_json`, `mistral`, `qwen3`은 frontend-crates 소스에 있다.

## 4. 대상별 생성 규칙

공통: 모델 `c.model_id`, GPU 1개, vendor가 `amd`면 `amd.com/gpu`, 아니면 `nvidia.com/gpu`. HF 토큰은 Secret 참조로만 넣고 값은 넣지 않는다. 출력은 `--out` 아래 파일들과 `README.md`(적용 명령, 필요한 Secret 생성 명령, 원본 run id).

### 4.1 `k8s`

`deployment.yaml`(Deployment 1개, 컨테이너 이미지 = `Platform.image(engine)`, args = 어댑터 `build_args`, 포트 = 어댑터 `container_port`, readinessProbe `GET /health`, `/dev/shm` emptyDir Memory) + `service.yaml`(ClusterIP, 포트 그대로). Secret 이름 `hf-token`.

### 4.2 `kserve`

vLLM 선택 후보만 지원한다. SGLang이 선택된 run이면 `TtalkkakError("kserve target supports vLLM runs; use --target llmd or k8s", 2)`. `VLLM_ADDITIONAL_ARGS`에는 `build_args` 결과에서 `--model <id>`, `--host`, `--port`와 값을 뺀 나머지를 한 줄로 넣는다. 이미지는 vendor에 따라 `ghcr.io/llm-d/llm-d-cuda:v0.9.0` / `ghcr.io/llm-d/llm-d-rocm:v0.9.0`. Secret 이름 `hf-token`.

### 4.3 `llmd`

`kustomization.yaml` + `patch.yaml`. 원격 base와 component를 `?ref=v1.0.0`으로 고정한다.

- resources: `https://github.com/llm-d/llm-d//guides/recipes/modelserver/base/single-host/default?ref=v1.0.0`
- components: `https://github.com/llm-d/llm-d//guides/recipes/modelserver/components/images/<경로>?ref=v1.0.0`. 경로는 v1.0.0 태그에서 확인한 `gpu-vllm/release`, `gpu-sglang/release`, `amd-vllm/release`, `amd-sglang/rocm720-mi30x`
- patch: `Deployment decode`, 컨테이너 `modelserver`, replicas 1. vLLM은 command `["vllm","serve"]`, args 첫 항목이 모델 id이고 나머지는 엔진 인자(`--model`, `--host`, `--port` 제외). SGLang은 command `["python3","-m","sglang.launch_server"]`, args는 어댑터 인자에서 실행 명령 3개를 빼고 `--port`를 8000으로 바꾼다. Secret 이름 `llm-d-hf-token`.

원격 kustomize base/component를 실제로 빌드(`kubectl kustomize`)하는 것은 네트워크와 kubectl이 필요해 마지막 수동 작업에서 확인한다.

### 4.4 `dynamo`

NVIDIA만 지원한다(AMD면 오류). `DynamoGraphDeployment` 하나에 `Frontend`(type frontend)와 `worker`(type worker). worker command는 `python3 -m dynamo.vllm` 또는 `dynamo.sglang`. args = 엔진 인자에서 `--host`, `--port`, `--enable-auto-tool-choice`, `--tool-call-parser <p>`, `--reasoning-parser <p>`, `--enable-metrics`, SGLang 실행 명령을 빼고, 파서는 `--dyn-tool-call-parser <p>`, `--dyn-reasoning-parser <p>`로 넣는다. 이미지는 `--image`로 받고 기본값을 두지 않는다(Dynamo runtime 이미지 태그를 1차 출처에서 확인하지 못했다. 예시 파일도 `my-tag`를 쓴다). Secret 이름 `hf-token-secret`.

## 5. 오류 처리

- run이 끝나지 않았거나 선택 후보가 없으면 `TtalkkakError(exit_code=2)`.
- 대상이 run의 엔진·vendor를 지원하지 않으면(4.2, 4.3 AMD 미확인, 4.4) 지원 범위를 말하는 오류, 종료 코드 2.
- `--out` 디렉터리에 파일이 이미 있으면 덮어쓰지 않고 오류(`--force`로 허용).

## 6. 테스트

| 대상 | 방법 |
|---|---|
| 각 target | 고정 run 디렉터리 fixture(vLLM 선택, SGLang 선택, AMD) → 생성 파일을 `yaml.safe_load_all`로 읽어 apiVersion/kind, 모델, GPU 리소스 키, Secret 참조, 인자 변환을 단언 |
| 인자 변환 | kserve에 `--model`·`--host`·`--port`가 없고 `--kv-cache-dtype` 등은 남음, dynamo에 `--tool-call-parser` 대신 `--dyn-tool-call-parser` |
| 오류 | SGLang run + kserve, AMD + dynamo, 미완료 run, 기존 파일 존재 |
| CLI | `ttalkkak export` 종료 코드와 생성 파일 목록 |

사람이 하는 마지막 작업: kind/minikube(GPU 노드) 또는 Dev Cloud에서 `kubectl apply -k`(llmd), `kubectl apply -f`(k8s, kserve, dynamo — 각 오퍼레이터 설치 후)로 1건 이상 실제 적용하고 결과를 기록.

## 7. 범위 밖

멀티 GPU·멀티 노드, P/D 분리, llm-d 라우터·EPP 설정, KServe 오퍼레이터·Dynamo 오퍼레이터 설치, Helm 차트 생성.
