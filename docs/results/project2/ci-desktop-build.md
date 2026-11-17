# 기준 6 근거: desktop 워크플로 빌드

출처: 맥의 Claude 세션이 `gh`로 확인해 2026-11-18에 전해 준 값이다. 이 데스크탑에는 `gh`와 `~/.gh`가 없고, 저장소가 비공개라 직접 조회하지 못했다(API 404).

- GitHub Actions run 44026828094 (https://github.com/Thisish3/ttalkkak/actions/runs/44026828094), 커밋 01f4b5a, 2026-11-17T05:49:57Z, conclusion `success`
- 잡 4개 모두 success: macos-aarch64, macos-x86_64, windows, linux
- artifacts: ttalkkak-console-macos-aarch64 3,179,606 B, ttalkkak-console-macos-x86_64 3,324,844 B, ttalkkak-console-windows 3,315,057 B, ttalkkak-console-linux 86,420,623 B
- 이전 run 44016442390(48642b0)은 windows만 실패했다(WiX LGHT0311: 한글 productName이 code page 1252에 없음). edb12b1(ko-KR)에서 고친 뒤 위 run에서 통과했다.
- 맥 앱이 콘솔에 토큰으로 붙어 실시간 화면이 도는지는 10절에서 사람이 맥에서 확인한다(아직 안 함).
