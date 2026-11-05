from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from ttalkkak.schemas import AppliedConfig, Candidate


class ContainerSpec(BaseModel):
    image: str
    args: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    container_port: int = 8000


class EngineAdapter(Protocol):
    name: str

    def build_args(self, c: Candidate) -> ContainerSpec: ...

    def read_applied(self, logs: str, metrics: str, models: dict) -> AppliedConfig: ...

    def health(self, base_url: str, client: httpx.Client) -> bool: ...
