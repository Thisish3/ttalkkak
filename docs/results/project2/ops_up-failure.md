# ops up 실패 기록 (2026-11-17)

## 1차: run 20261117-165030
명령: `.venv/bin/ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct --chip $HOME/.ttalkkak/nvidia-gpu-no-sglang.yaml --gpu-memory-utilization 0.8` (로그 `ops_up.log`, exit=1)

- c1, c2: 기동 실패 (oom). vLLM 로그(`runs/20261117-165030/launch.json`):
  - `Pinned memory is not available on this platform; falling back to device memory for UVA buffers.`
  - 가중치를 만들던 중(`qwen2.py` `Qwen2MLP` → `torch.empty`) torch OOM: `... of which 4.67 GiB is free ... Of the allocated memory 2.21 GiB is allocated by PyTorch`
  - `memory allocation failed with OOM on device 0 while trying to allocate 56623104 bytes (free: 4972347392, total: 8589410304)`
- c3: `no image for sglang on nvidia` (R2, 의도한 실패)

## 2차: 손으로 docker run 재현 (Windows docker.exe)
`docker run -d --gpus all --ipc=host -v ttalkkak-hf-cache:/root/.cache/huggingface vllm/vllm-openai:v0.33.0 --model Qwen/Qwen2.5-1.5B-Instruct --max-model-len 4096 --gpu-memory-utilization 0.8`
- `docker.exe` 자신이 `fatal error: out of memory allocating heap arena map`으로 죽고 PowerShell도 `System.OutOfMemoryException`, `Thread failed to start`.
- WSL 쪽 `docker run`/`docker ps`는 응답 없이 멈춤.

## Windows 메모리 측정 (`wmic OS get ...`, KB)
| 시점 | 실제 메모리 여유 | 커밋 여유 (FreeVirtualMemory) | 커밋 한도 |
|---|---|---|---|
| 재현 직후, Docker Desktop 켜짐 | 2,186,388 | **38,564** | 22,954,268 |
| Docker Desktop 종료 뒤 (WSL VM도 같이 멈춤) | 11,016,884 | 9,291,844 | 22,954,268 |

- RAM 16 GB, 페이지 파일 `C:\pagefile.sys` 할당 6,135 MB(C: 여유 약 4 GB). 가장 큰 프로세스: `vmmem` 작업 집합 7,998 MB.
- **측정된 것:** Docker Desktop과 k3s가 같이 돌 때 Windows 커밋 여유가 38 MB까지 떨어졌고, 그때 docker.exe와 PowerShell이 메모리 할당에 실패했다.
- **원인 미확인:** vLLM의 CUDA OOM(GPU 여유 4.67 GiB에서 56 MB 할당 실패)이 이 커밋 고갈 때문인지는 재지 않았다.

## 3차: k3s 끈 채로 ops up (run 20261117-182242, 로그 `ops_up-2.log`)
- 시작 전(k3s inactive, Docker Desktop만): Windows 커밋 여유 4,906,788 KB, WSL `free -m` total 7,889 MB.
- 18:22:41 시작. 10초마다 잰 커밋 여유(`commit-free-run2.log`): 4,597,136 → 3,596,420 → 2,873,052 → 2,367,704 → 2,396,960 KB(18:23:27).
- vLLM 컨테이너 c1이 뜬 지 약 53초 뒤 커밋 여유 354,364 KB, 실제 메모리 여유 2,488,128 KB. 이때 Claude Code가 메모리 부족으로 `ops up` 셸을 멈췄다(결과 없음). 남은 컨테이너 `6dcffc6fbe08`을 지운 뒤 커밋 여유 3,972,996 KB.
- **측정된 것:** vLLM 컨테이너 하나(Qwen2.5-1.5B, 0.8)를 띄우는 동안 Windows 커밋 여유가 약 4.5 GB 줄었다.
- 메모리가 어디에 쓰였는지는 재지 않았다(원인 미확인).

## 3차 뒤 디스크 상태와 정리
- `wmic logicaldisk`: C: 여유 112,119,808 B. 같은 시각 Ubuntu 안 `df -h /` 사용량은 123G 그대로였지만 `ext4.vhdx` 파일은 131.8 → 141.6 GB, `docker_data.vhdx`는 89.7 → 96.3 GB로 늘어 있었다.
- 조치: `wsl -u root fstrim -v /`(883.6 GiB trimmed), docker-desktop 배포판 `fstrim -av`, Docker Desktop 종료, `wsl --shutdown`, diskpart `attach vdisk readonly` / `compact vdisk` / `detach vdisk`를 두 vhdx에 했다. 정리 뒤 C: 여유 62.0 GB.
- Windows 페이지 파일은 자동 관리(`AutomaticManagedPagefile TRUE`)이고, 그때 할당은 6,192 MB였다. C:가 거의 찼던 것과 커밋 고갈 사이에 관계가 있는지는 재지 않았다(원인 미확인).
