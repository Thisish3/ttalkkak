import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "desktop" / "web"
OLD_INDEX = "71d0e4f:src/ttalkkak/ui/static/index.html"
OLD_MUTATIONS = {
    "/api/runs",
    "/api/runs/{}/export",
    "/api/ops/tenants",
    "/api/ops/tenants/{}/keys",
    "/api/ops/keys/{}/revoke",
    "/api/ops/tenants/{}/limit",
    "/api/ops/tenants/{}/cache",
}


def endpoints(text):
    found = set()
    for raw in re.findall(r"[\"'`](/api/[^\"'`?]*)", text):
        found.add(re.sub(r"\$\{[^}]*\}", "{}", raw))
    return found


def old_endpoints():
    old = subprocess.run(
        ["git", "show", OLD_INDEX], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return endpoints(old)


def test_every_endpoint_of_the_old_console_is_still_used():
    old = old_endpoints()
    assert "/api/ops/tenants/{}/keys" in old and len(old) >= 12
    missing = old - endpoints((WEB / "app.js").read_text(encoding="utf-8"))
    assert not missing, f"app.js no longer calls: {sorted(missing)}"


def test_all_requests_go_through_api():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert len(re.findall(r"\bfetch\(", js)) == 1  # inside api() only
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)  # no inline script
    assert not re.search(r"(?:src|href)=[\"']https?://", html)  # no CDN


def mutate_body(js):
    start = js.index("async function mutate(")
    return js[start : js.index("\n}\n", start)]


def test_mutating_calls_only_through_the_confirm_modal():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert len(re.findall(r"""method:\s*["']POST["']""", js)) == 1
    body = mutate_body(js)
    assert body.index("confirmDialog(") < body.index('method: "POST"')
    assert "if (!yes) return" in body
    called = {
        re.sub(r"\$\{[^}]*\}", "{}", m)
        for m in re.findall(r"""\bmutate\(\s*[`"'](/api/[^`"'?]*)""", js)
    }
    assert OLD_MUTATIONS <= called


def test_confirm_modal_states_what_happens():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    calls = re.findall(r"(?<!function )\bmutate\(([^;]*?\n\s*\}\);)", js, re.DOTALL)
    assert len(calls) >= len(OLD_MUTATIONS)
    for call in calls:
        assert "title:" in call and "message:" in call, call


def test_index_has_the_six_panels_and_components():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for pid in ("live", "overview", "cost", "tenants", "usage", "runs"):
        assert f'id="panel-{pid}"' in html
    css = (WEB / "app.css").read_text(encoding="utf-8")
    for sel in (".btn.danger", ".modal", ".toast", ".skeleton", ".errbox", "thead th"):
        assert sel in css
