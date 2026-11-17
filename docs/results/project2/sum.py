import json, sys
p = sys.argv[1]
d = json.load(open(p))
print(f"### {d['id']} (복제본 {d['replicas']}, 모델 {d['model']}, 단계 {d['probe_seconds']}초, 최대 사용자 {d['max_users']}, autoscale_on={d['autoscale_on']})\n")
print("| 순서 | 동시 사용자 | 달성률 | 신뢰 구간 | 판정 | 오류율 | 중단 | 토큰/분 | determined_requests | TTFT p99 ms |")
print("|---|---|---|---|---|---|---|---|---|---|")
low = []
for i, s in enumerate(d.get("stages") or [], 1):
    v = "통과" if s.get("passed") and s.get("resolved") else ("실패" if s.get("resolved") else "모름")
    ci = [s.get("attainment_lower"), s.get("attainment_upper")]
    def f(x, n=3):
        return "-" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))
    dr = s.get("determined_requests")
    if v == "통과" and dr is not None and dr < 74: low.append((s.get("users"), dr))
    print(f"| {i} | {s.get('users')} | {f(s.get('attainment'))} | [{f(ci[0])}, {f(ci[1])}] | {v} | {f(s.get('error_rate'))} | {'예' if s.get('aborted') else '아니오'} | {f(s.get('tokens_per_min'),0)} | {dr} | {f(s.get('ttft_p99_ms'),1)} |")
print(f"\n- stop_reason: `{d.get('stop_reason')}`, best_passing_streams {d.get('best_passing_streams')}, lowest_failing_streams {d.get('lowest_failing_streams')}")
print(f"- 통과 단계 중 determined_requests < 74: {low if low else '없음'}")
print("\n| 확인 | 결과 | 수준(사용자) | 요청 속도 rps | 달성 속도 rps | 달성률 | 신뢰 구간 | 오류율 | TTFT p99 ms | 토큰/분 |")
print("|---|---|---|---|---|---|---|---|---|---|")
for i, c in enumerate(d.get("confirms") or [], 1):
    ci = [c.get("attainment_lower"), c.get("attainment_upper")]
    print(f"| {i} | {'확인 통과' if c.get('held') else '확인 실패'} | {c.get('users')} | {c.get('rate_rps')} | {c.get('achieved_rps')} | {c.get('attainment')} | {ci} | {c.get('error_rate')} | {c.get('ttft_p99_ms')} | {c.get('tokens_per_min')} |")
conf = any(c.get("held") for c in d.get("confirms") or [])
print(f"\n- confirmed: {str(conf).lower()}, capacity_streams {d.get('capacity_streams')}, capacity_tpm {d.get('capacity_tpm')}")
print(f"- no_capacity_reason: {d.get('no_capacity_reason')}, error: {d.get('error')}")
print(f"- knee: {json.dumps(d.get('knee'), ensure_ascii=False)}")
