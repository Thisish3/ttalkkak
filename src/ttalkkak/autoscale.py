"""자동 증감 판단 규칙 (순수 함수, 설계 4.6절)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class Rules(BaseModel):
    up_pct: float = 80.0
    up_for_s: int = 120
    down_pct: float = 30.0
    down_for_s: int = 600
    idle_s: int = 300
    idle_after_pct: float = 60.0
    cool_up_s: int = 300
    cool_down_s: int = 600


class Sample(BaseModel):
    t: datetime
    util_pct: float | None
    waiting: int
    ready: int
    desired: int
    idle_replicas: int


class State(BaseModel):
    last_change: datetime | None = None
    last_dir: Literal["up", "down"] | None = None
    history: list[Sample] = []


class Decision(BaseModel):
    t: datetime
    action: Literal["up", "down", "hold"]
    from_replicas: int
    to_replicas: int
    reason: str


def _trim(history: list[Sample], now: datetime, keep_s: int) -> list[Sample]:
    """keep_s초 안의 샘플과, 창 길이를 확인할 수 있게 바로 바깥의 샘플 하나를 남긴다."""
    cutoff = now - timedelta(seconds=keep_s)
    inside = [h for h in history if h.t >= cutoff]
    outside = [h for h in history if h.t < cutoff]
    return outside[-1:] + inside


def _sustained(history: list[Sample], now: datetime, for_s: int, ok) -> bool:
    """최근 for_s초의 모든 샘플이 조건을 만족하고 기록이 for_s초 넘게 이어졌는가."""
    cutoff = now - timedelta(seconds=for_s)
    window = [h for h in history if h.t >= cutoff]
    before = [h for h in history if h.t < cutoff]
    if not before:
        return False
    return all(ok(h) for h in window + before[-1:])


def _minutes(sec: int) -> str:
    return f"{sec // 60}분" if sec % 60 == 0 else f"{sec}초"


def decide(
    state: State,
    s: Sample,
    rules: Rules,
    *,
    min_r: int = 1,
    max_r: int,
) -> tuple[Decision, State]:
    keep_s = max(rules.up_for_s, rules.down_for_s)
    history = _trim([*state.history, s], s.t, keep_s)
    new_state = state.model_copy(update={"history": history})

    def hold(reason: str) -> tuple[Decision, State]:
        return (
            Decision(
                t=s.t,
                action="hold",
                from_replicas=s.desired,
                to_replicas=s.desired,
                reason=reason,
            ),
            new_state,
        )

    def change(action: Literal["up", "down"], to: int, reason: str):
        st = new_state.model_copy(update={"last_change": s.t, "last_dir": action})
        return (
            Decision(
                t=s.t,
                action=action,
                from_replicas=s.desired,
                to_replicas=to,
                reason=reason,
            ),
            st,
        )

    if s.util_pct is None:
        return hold("용량 기록 없음 또는 지표 없음")
    if s.ready < s.desired:
        return hold(f"새 복제본 준비 중 (준비 {s.ready}/{s.desired})")

    if state.last_change is not None and state.last_dir is not None:
        cool = rules.cool_up_s if state.last_dir == "up" else rules.cool_down_s
        elapsed = (s.t - state.last_change).total_seconds()
        if elapsed < cool:
            what = "늘린" if state.last_dir == "up" else "줄인"
            return hold(
                f"{what} 뒤 쿨다운 중 ({int(elapsed)}초 지남, {cool}초까지 변경 안 함)"
            )

    def up_ok(h: Sample) -> bool:
        return h.util_pct is not None and (h.util_pct >= rules.up_pct or h.waiting > 0)

    if _sustained(history, s.t, rules.up_for_s, up_ok):
        if s.desired >= max_r:
            return hold(f"늘리려 했으나 최대 복제본 {max_r}개")
        span = _minutes(rules.up_for_s)
        if s.util_pct >= rules.up_pct:
            why = f"용량 사용률 {s.util_pct:g} %가 {span} 넘게 계속"
        else:
            why = f"대기 중 요청 {s.waiting}개가 {span} 넘게 계속"
        return change("up", s.desired + 1, why)

    def down_ok(h: Sample) -> bool:
        return (
            h.util_pct is not None and h.util_pct <= rules.down_pct and h.waiting == 0
        )

    if _sustained(history, s.t, rules.down_for_s, down_ok):
        if s.desired <= min_r:
            return hold(f"줄이려 했으나 최소 복제본 {min_r}개")
        span = _minutes(rules.down_for_s)
        return change(
            "down",
            s.desired - 1,
            f"용량 사용률 {s.util_pct:g} %가 {span} 넘게 계속 {rules.down_pct:g} % 이하",
        )

    if s.idle_replicas > 0 and s.waiting == 0 and s.desired > min_r and s.desired > 1:
        projected = s.util_pct * s.desired / (s.desired - 1)
        if projected <= rules.idle_after_pct:
            return change(
                "down",
                s.desired - 1,
                f"놀고 있는 복제본 {s.idle_replicas}개, 줄이면 예상 용량 사용률 "
                f"{projected:g} % (기준 {rules.idle_after_pct:g} % 이하)",
            )

    return hold("조건 없음")
