import warnings

from langchain.agents import create_agent
from langchain_core._api import LangChainBetaWarning
from langchain_openai import ChatOpenAI

from ttalkkak.skills.loader import Skill


def system_prompt(skills: list[Skill]) -> str:
    parts = ["You are a helpful agent. Follow the skill instructions below."]
    for s in skills:
        parts += [f"## skill: {s.name}", s.description, s.body.strip()]
    return "\n\n".join(parts)


def mcp_config(skills: list[Skill]) -> dict:
    servers = {
        m.name: {"command": m.command[0], "args": m.command[1:]}
        for s in skills
        for m in s.ext.mcp_servers
    }
    return {"mcpServers": servers}


async def build_agent(
    base_url: str, model: str, skills: list[Skill], api_key: str = "EMPTY"
):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangChainBetaWarning)
        from langchain.mcp import MCPAdapter
    cfg = mcp_config(skills)
    adapter = MCPAdapter(cfg) if cfg["mcpServers"] else None
    tools = []
    if adapter:
        await adapter.__aenter__()
        tools = await adapter.list_tools()
    llm = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0)
    return create_agent(llm, tools, system_prompt=system_prompt(skills)), adapter
