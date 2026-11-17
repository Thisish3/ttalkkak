# 프로젝트 2 데스크탑 실기 검증 ledger

지시문(`docs/desktop/project2-desktop.md`)과 다르게 한 결정. 무엇을, 왜, 틀리면 드는 비용.

## R1. `ops up`에 `--gpu-memory-utilization` 옵션 추가 (2026-11-17)

- **무엇을:** 지시문 4절의 `ops export --run <run>`에 쓸 성공한 run이 없어서, `ops up`을 다시 돌리기 전에 vLLM 후보의 GPU 메모리 비율을 옵션으로 받게 고쳤다. 기본값은 0.9로 그대로이고, 계획의 문맥 길이 예측(`max_context_fit`)도 같은 비율을 쓴다. SGLang 후보는 바꾸지 않았다.
- **왜:** 이전 run `20261111-235400`에서 vLLM 후보 c1, c2가 모두 다음 오류로 기동에 실패했다(`runs/20261111-235400/launch.json`).
  `ValueError: Free memory on device cuda:0 (6.9/8.0 GiB) on startup is less than desired GPU memory utilization (0.9, 7.2 GiB).`
  0.9는 `src/ttalkkak/plan/candidates.py`에 고정돼 있어서, 이 데스크탑(Windows가 GPU 메모리 일부를 씀)에서는 `ops up`이 항상 실패한다.
- **확인:** `.venv/bin/python -m pytest -q` → `561 passed, 4 skipped`. `ruff check`, `ruff format --check` 통과.
- **틀리면 드는 비용:** 비율을 너무 낮게 주면 KV 캐시가 줄어 문맥 길이와 용량이 작게 나온다. 게이트웨이 배포(`ops export --target gateway`)는 복제본 수로 비율을 다시 정하므로 영향이 없다(`gateway/manifests.py`).
