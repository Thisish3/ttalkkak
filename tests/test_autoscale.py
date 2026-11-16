from datetime import UTC, datetime, timedelta

from ttalkkak.autoscale import Decision, Rules, Sample, State, decide

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)


def sample(sec, util=50.0, waiting=0, ready=1, desired=1, idle=0):
    return Sample(
        t=T0 + timedelta(seconds=sec),
        util_pct=util,
        waiting=waiting,
        ready=ready,
        desired=desired,
        idle_replicas=idle,
    )


def run(samples, rules=None, state=None, min_r=1, max_r=3):
    rules = rules or Rules()
    state = state or State()
    decision = None
    for s in samples:
        decision, state = decide(state, s, rules, min_r=min_r, max_r=max_r)
        if decision.action != "hold":
            break
    return decision, state


def test_hold_without_utilization():
    d, _ = run([sample(0, util=None)])
    assert d.action == "hold"
    assert "지표 없음" in d.reason


def test_up_after_two_minutes_over_80():
    d, _ = run([sample(i, util=84) for i in range(120)])
    assert d.action == "hold"
    d, st = run([sample(i, util=84) for i in range(122)])
    assert d.action == "up"
    assert (d.from_replicas, d.to_replicas) == (1, 2)
    assert "84" in d.reason
    assert st.last_dir == "up"
    assert st.last_change == T0 + timedelta(seconds=121)


def test_up_on_waiting_two_minutes():
    d, _ = run([sample(i, util=40, waiting=2) for i in range(122)])
    assert d.action == "up"
    assert "대기" in d.reason


def test_no_up_at_max():
    d, _ = run([sample(i, util=90, ready=3, desired=3) for i in range(122)], max_r=3)
    assert d.action == "hold"
    assert "최대 복제본" in d.reason


def test_down_after_ten_minutes_under_30():
    samples = [sample(i, util=20, ready=2, desired=2) for i in range(599)]
    d, _ = run(samples)
    assert d.action == "hold"
    samples = [sample(i, util=20, ready=2, desired=2) for i in range(602)]
    d, st = run(samples)
    assert d.action == "down"
    assert (d.from_replicas, d.to_replicas) == (2, 1)
    assert "20" in d.reason
    assert st.last_dir == "down"


def test_no_down_below_one():
    d, _ = run([sample(i, util=5) for i in range(602)])
    assert d.action == "hold"


def test_idle_down_when_projected_ok():
    d, _ = run([sample(0, util=25, ready=3, desired=3, idle=1)])
    assert d.action == "down"
    assert (d.from_replicas, d.to_replicas) == (3, 2)
    assert "37.5" in d.reason


def test_idle_hold_when_projected_too_high():
    d, _ = run([sample(0, util=50, ready=3, desired=3, idle=1)])
    assert d.action == "hold"
    assert "조건 없음" in d.reason


def test_cooldown_after_up_blocks_down():
    st = State(last_change=T0, last_dir="up")
    d, _ = run([sample(100, util=25, ready=3, desired=3, idle=1)], state=st)
    assert d.action == "hold"
    assert "쿨다운" in d.reason
    d, _ = run([sample(301, util=25, ready=3, desired=3, idle=1)], state=st)
    assert d.action == "down"


def test_cooldown_after_down_is_ten_minutes():
    st = State(last_change=T0, last_dir="down")
    d, _ = run([sample(400, util=95, waiting=3)] * 1, state=st)
    assert d.action == "hold"
    assert "쿨다운" in d.reason


def test_hold_while_not_ready():
    d, _ = run([sample(i, util=95, ready=1, desired=2) for i in range(300)])
    assert d.action == "hold"
    assert "준비 중" in d.reason


def test_one_step_at_a_time():
    d, _ = run([sample(i, util=99, waiting=9) for i in range(500)], max_r=10)
    assert isinstance(d, Decision)
    assert d.action == "up"
    assert d.to_replicas - d.from_replicas == 1


def test_exactly_two_minutes_holds():
    d, _ = run([sample(i, util=84) for i in range(121)])
    assert d.action == "hold"


def test_idle_hold_while_requests_wait():
    d, _ = run([sample(0, util=25, waiting=1, ready=3, desired=3, idle=1)])
    assert d.action == "hold"
