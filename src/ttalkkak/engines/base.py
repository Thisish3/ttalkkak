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
    container_port: int

    def build_args(self, c: Candidate) -> ContainerSpec: ...

    def collect(self, base_url: str, client: httpx.Client) -> dict: ...

    def read_applied(self, logs: str, collected: dict) -> AppliedConfig: ...

    def health(self, base_url: str, client: httpx.Client) -> bool: ...
