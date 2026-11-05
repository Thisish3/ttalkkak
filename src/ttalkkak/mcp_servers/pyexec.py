import subprocess

from fastmcp import FastMCP

mcp = FastMCP("pyexec")


def run_python(code: str, timeout_s: int = 10, run=subprocess.run) -> str:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--memory=256m",
        "--cpus=1",
        "python:3.12-slim",
        "python",
        "-c",
        code,
    ]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s + 20)
    except subprocess.TimeoutExpired:
        return "error: timeout"
    return p.stdout + (f"\nstderr:\n{p.stderr}" if p.stderr else "")


@mcp.tool()
def python(code: str) -> str:
    """Run Python code in an isolated container (no network) and return its output."""
    return run_python(code)


if __name__ == "__main__":
    mcp.run()
