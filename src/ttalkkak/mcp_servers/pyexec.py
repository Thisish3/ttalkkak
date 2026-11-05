import subprocess
import uuid

from fastmcp import FastMCP

mcp = FastMCP("pyexec")
IMAGE = "python:3.12-slim"


def run_python(code: str, timeout_s: int = 10, run=subprocess.run) -> str:
    name = f"ttalkkak-pyexec-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--name",
        name,
        "--memory=256m",
        "--cpus=1",
        "--pids-limit=64",
        "--read-only",
        "--tmpfs=/tmp:size=16m",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        IMAGE,
        "python",
        "-c",
        code,
    ]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s + 20)
    except subprocess.TimeoutExpired:
        run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)
        return "error: timeout"
    return p.stdout + (f"\nstderr:\n{p.stderr}" if p.stderr else "")


@mcp.tool()
def python(code: str) -> str:
    """Run Python code in an isolated container (no network) and return its output."""
    return run_python(code)


if __name__ == "__main__":
    subprocess.run(["docker", "pull", IMAGE], capture_output=True, timeout=600)
    mcp.run()
