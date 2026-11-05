from pathlib import Path

from ttalkkak.errors import TtalkkakError
from ttalkkak.export import dynamo, k8s, kserve, llmd
from ttalkkak.export.common import load_run

TARGETS = {"k8s": k8s, "kserve": kserve, "llmd": llmd, "dynamo": dynamo}
_SECRET = {
    "k8s": "hf-token",
    "kserve": "hf-token",
    "llmd": "llm-d-hf-token",
    "dynamo": "hf-token-secret",
}
_NOTES = {
    "k8s": "Any cluster with a GPU device plugin.",
    "kserve": "Needs KServe with the LLMInferenceService CRD (v1alpha2), plus Gateway "
              "API and the Inference Extension for the router. The serving image is "
              "llm-d's (ghcr.io/llm-d/llm-d-*:v0.9.0), not the vLLM version the run "
              "verified; re-check the flags if versions differ.",
    "llmd": "Builds on llm-d v1.0.0 recipes fetched by kustomize; router/EPP is not "
            "included (see llm-d guides/optimized-baseline/router).",
    "dynamo": "Needs the Dynamo operator (DynamoGraphDeployment v1beta1).",
}  # fmt: skip


def _readme(target: str, run_id: str, files: list[str]) -> str:
    apply = (
        "kubectl apply -k ."
        if target == "llmd"
        else "kubectl apply -f " + " -f ".join(f for f in files)
    )
    return "\n".join([
        f"# ttalkkak export: {target}",
        "",
        f"Generated from run `{run_id}` (the candidate that passed ttalkkak's checks).",
        "",
        _NOTES[target],
        "",
        "```bash",
        f"kubectl create secret generic {_SECRET[target]} --from-literal=HF_TOKEN=$HF_TOKEN",
        apply,
        "```",
        "",
    ])  # fmt: skip


def write_export(
    run_dir: Path, target: str, out: Path, force: bool = False, image: str | None = None
) -> list[Path]:
    if target not in TARGETS:
        raise TtalkkakError(
            f"unknown target {target!r}; choose from {sorted(TARGETS)}", 2
        )
    bundle = load_run(run_dir)
    files = TARGETS[target].render(bundle, image=image)
    files["README.md"] = _readme(target, bundle.run_id, sorted(files))
    paths = [out / name for name in files]
    existing = [p for p in paths if p.exists()]
    if existing and not force:
        raise TtalkkakError(
            f"이미 있는 파일: {', '.join(p.name for p in existing)} (덮어쓰려면 --force)",
            2,
        )
    out.mkdir(parents=True, exist_ok=True)
    for p in paths:
        p.write_text(files[p.name], encoding="utf-8")
    return paths
