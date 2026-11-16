(async () => {
  const $ = (id) => document.getElementById(id);
  const fail = (msg) => { $("err").textContent = "✕ " + msg; $("err").hidden = false; };
  if (!inApp) {
    fail("이 화면은 데스크탑 앱에서만 쓸 수 있습니다");
    $("go").disabled = true;
    return;
  }
  const saved = await loadConnection();
  if (saved) $("url").value = saved.url;
  const refreshRisk = async () => {
    const risky = await tauri.invoke("url_cleartext_risk", { url: $("url").value.trim() });
    $("clear-row").hidden = !risky;
    if (!risky) $("clear-ok").checked = false;
    return risky;
  };
  $("url").addEventListener("input", refreshRisk);
  refreshRisk();
  $("form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("err").hidden = true;
    $("warn").hidden = true;
    if ((await refreshRisk()) && !$("clear-ok").checked) {
      fail("암호화되지 않은 주소입니다. 위험을 이해했다는 항목을 체크해야 연결할 수 있습니다");
      return;
    }
    $("go").disabled = true;
    const url = $("url").value.trim();
    const token = $("token").value.trim();
    try {
      await pingServer(url, token, $("clear-ok").checked);
      await tauri.invoke("save_connection", { url, token });
      const conn = await loadConnection();
      if (conn && conn.token_storage === "file") {
        $("warn").textContent = "▲ 키체인을 쓸 수 없어 토큰을 설정 파일(권한 0600)에 저장했습니다";
        $("warn").hidden = false;
        setTimeout(() => location.replace("index.html"), 2500);
      } else {
        location.replace("index.html");
      }
    } catch (e) {
      fail(String(e));
      $("go").disabled = false;
    }
  });
})();
