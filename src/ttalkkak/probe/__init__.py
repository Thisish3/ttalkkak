import subprocess

from ttalkkak.probe.gpu import detect_gpu
from ttalkkak.probe.hf import fetch_model_info
from ttalkkak.schemas import ProbeResult


def probe(model: str, api=None, run=subprocess.run) -> ProbeResult:
    if model.startswith("nim:"):
        return ProbeResult(model=None, gpu=None, remote=model)
    return ProbeResult(model=fetch_model_info(model, api=api), gpu=detect_gpu(run))
