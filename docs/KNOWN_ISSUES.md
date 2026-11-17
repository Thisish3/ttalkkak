# 알려진 문제 (2026-11-18)

프로젝트 2 실기 검증(데스크탑 RTX 3060 Ti, 맥 앱)과 리뷰에서 나온 것 중 아직 고치지 않은 것. 근거: `docs/results/project2/`, `docs/project2-ledger.md`.

## 데스크톱 앱
- 부하 시험 그래프의 세로축 최대값 라벨이 잘린다. `.chart`가 `width: 100%`라 작은 viewBox의 10px 글자가 크게 확대된다(`desktop/web/app.css`, `app.js` loadtestChart).
- 실시간 갱신이 "응답 뒤 2초"라 원격(터널, 응답 약 1초)에서는 약 3.3초 주기다.
- 연결이 끊기면 오류 상자에 "×"만 나오고 이유 문구가 없다.
- `/api/runs`가 runs 폴더의 `loadtests`, `autoscale`을 실행처럼 보여 준다.
- 미확인: OS 알림 전달(첫 알림 때 권한 요청), 평문 HTTP 동의 초기화, API 키 창 Esc, 앱에서 서빙 실행.
- 설치 파일 서명 없음(맥 우클릭 열기, Windows SmartScreen).

## 서버·클러스터
- 사용량 조회가 비어 있다(게이트웨이 재설치 뒤 접근 로그, 원인 미확인).
- Running이지만 Ready가 아닌 파드가 부하 시험 복제본 수에 들어가 용량 기록 키가 틀릴 수 있다(`ops_routes.py`).
- `_publish_tenants`가 클러스터 `ttalkkak-tenants` Secret을 콘솔 tenants.db로 통째로 덮어쓴다.
- 공유 hostPath 모델 캐시를 정리하지 않는다(데스크탑 디스크가 한 번 찼다).
- `deployments/scale`의 `get` 권한은 더 필요 없다.
- 오래 돈 vLLM 파드 옆에 새 파드를 붙이면 1명부터 첫 글자 p99 약 575 ms(유휴 GPU 메모리 7,985 MiB), 둘 다 새로 띄우면 정상(원인 미확인, ledger R25).

## 측정 결과의 한계
- GPU 한 장을 복제본 2개로 나눠도 총 처리량은 같다(267,009 vs 272,848 토큰/분). 차이는 열린 부하에서 SLO를 지킨 동시 사용자(16 vs 36). 원인 미확인.
- goodput 탐색은 단계 요청 수가 74개 미만이면 판정을 못 한다(달성률 목표 0.95). 단계 시간으로 늘린다.
