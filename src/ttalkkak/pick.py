from ttalkkak.schemas import CandidateVerdict, Pick

ACCURACY_DROP_MAX = 0.05


def pick(
    verdicts: list[CandidateVerdict],
    baseline: float | None,
    launched_urls: dict[str, str],
) -> Pick:
    rejected, ok = {}, []
    for v in verdicts:
        failed = [c.name for c in v.agent if not c.passed]
        if failed:
            rejected[v.candidate_id] = f"agent 검사 실패: {', '.join(failed)}"
        elif (
            v.accuracy is not None
            and baseline is not None
            and baseline - v.accuracy > ACCURACY_DROP_MAX
        ):
            rejected[v.candidate_id] = (
                f"정확도 하락 {(baseline - v.accuracy) * 100:.1f}%p "
                f"(기준 {baseline:.2f} → {v.accuracy:.2f})"
            )
        else:
            ok.append(v)
    if not ok:
        return Pick(chosen=None, rejected=rejected)
    # max() keeps the first of equal scores, so ties go to the earlier candidate
    best = max(ok, key=lambda v: v.perf.get("output_tok_s", 0.0))
    return Pick(
        chosen=best.candidate_id,
        rejected=rejected,
        base_url=launched_urls.get(best.candidate_id),
    )
