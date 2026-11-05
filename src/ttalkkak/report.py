from ttalkkak.schemas import Launched, Pick, Plan, Verification


def _fmt(x) -> str:
    if x is None:
        return "-"
    return f"{x:.1f}" if isinstance(x, float) else str(x)


def render_report(
    plan: Plan, launched: list[Launched], ver: Verification, chosen: Pick
) -> str:
    by_v = {v.candidate_id: v for v in ver.verdicts}
    lines = [
        "# ttalkkak report",
        "",
        f"**선택:** {chosen.chosen or '없음 (모든 후보 탈락)'}",
        "",
    ]
    warns = [f"- {cid}: {m}" for cid, v in by_v.items() for m in v.mismatches]
    lines += ["## 경고", *(warns or ["- 없음"]), ""]
    reqs = [f"- {k} ← {', '.join(s)}" for k, s in plan.requirements.sources.items()]
    lines += ["## 요구사항", *(reqs or ["- 없음"]), ""]
    lines += [
        "## 예측 vs 실측",
        "",
        (
            "| 후보 | 엔진 | max_model_len | KV 토큰 예측 / 실측 | 출력 tok/s "
            "| TTFT ms | 정확도 |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for c in plan.candidates:
        v = by_v.get(c.id)
        real_kv = v.applied.values.get("kv_cache_tokens") if v else None
        pred_kv = c.predicted.get("kv_tokens_fit")
        cells = [
            c.id,
            c.engine,
            str(c.args.get("max_model_len", "-")),
            (
                f"{_fmt(int(pred_kv) if pred_kv is not None else None)} / "
                f"{_fmt(real_kv.value if real_kv else None)}"
            ),
            _fmt(v.perf.get("output_tok_s") if v else None),
            _fmt(v.perf.get("ttft_ms") if v else None),
            _fmt(v.accuracy if v else None),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## 탈락·실패"]
    for cid, why in chosen.rejected.items():
        lines.append(f"- {cid}: {why}")
    for item in launched:
        if item.status == "failed":
            lines += [
                f"- {item.candidate_id}: 기동 실패 ({item.failure})",
                "```",
                *item.log_tail[-10:],
                "```",
            ]
    for c in plan.candidates:
        for b in c.blockers:
            lines.append(f"- {c.id}: 계획 단계에서 제외 ({b})")
        if c.blockers:
            lines += [f"  - {r}" for r in c.reasons]
    lines += ["", "## 에이전트 검사"]
    for cid, v in by_v.items():
        for r in v.agent:
            lines.append(f"- {cid} {r.name}: {'통과' if r.passed else '실패'}")
    if chosen.base_url:
        model = next((c.model_id for c in plan.candidates if c.id == chosen.chosen), "")
        lines += [
            "",
            "## 연결",
            "```python",
            "from langchain_openai import ChatOpenAI",
            (
                f'llm = ChatOpenAI(base_url="{chosen.base_url}/v1", '
                f'api_key="EMPTY", model="{model}")'
            ),
            "```",
        ]
    return "\n".join(lines) + "\n"
