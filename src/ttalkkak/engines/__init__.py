from ttalkkak.engines.sglang import SglangAdapter
from ttalkkak.engines.vllm import VllmAdapter

ADAPTERS = {"vllm": VllmAdapter(), "sglang": SglangAdapter()}
