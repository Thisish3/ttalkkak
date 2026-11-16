// OS notifications for autoscale decisions and 429 rejection spikes (desktop app only).
const NOTIFY_KEY = "ttalkkak.notifications";
const SPIKE_WINDOW_MS = 60000;
const SPIKE_MIN = 10;
const SPIKE_COOLDOWN_MS = 300000;

function notifyEnabled(storage) {
  try {
    return storage.getItem(NOTIFY_KEY) !== "off";
  } catch (e) {
    return true;
  }
}

function setNotifyEnabled(storage, on) {
  try {
    storage.setItem(NOTIFY_KEY, on ? "on" : "off");
  } catch (e) {
    // the toggle still applies for this session
  }
}

function createNotifier({ send, now, enabled }) {
  const seen = new Set();
  let seeded = false;
  let samples = [];
  let latched = false;
  let lastSpike = -Infinity;

  const emit = (title, body) => {
    if (!enabled()) return;
    try {
      send(title, body);
    } catch (e) {
      console.error(e);
    }
  };

  function decisions(list) {
    const fresh = [];
    for (const d of list) {
      const key = `${d.t}|${d.action}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (d.action === "up" || d.action === "down") fresh.push(d);
    }
    if (!seeded) {
      seeded = true;
      return;
    }
    for (const d of fresh.reverse()) {
      const span = d.from_replicas == null && d.to_replicas == null ? "" : `복제본 ${d.from_replicas ?? "?"} → ${d.to_replicas ?? "?"}. `;
      emit(d.action === "up" ? "자동 증감: 복제본을 늘렸습니다" : "자동 증감: 복제본을 줄였습니다", `${span}${d.reason || ""}`.trim());
    }
  }

  function rejections(count) {
    const t = now();
    samples = samples.filter((s) => t - s[0] <= SPIKE_WINDOW_MS);
    samples.push([t, count]);
    const rise = count - Math.min(...samples.map((s) => s[1]));
    if (rise < SPIKE_MIN) {
      latched = false;
      return;
    }
    if (latched) return;
    latched = true;
    if (t - lastSpike < SPIKE_COOLDOWN_MS) return;
    lastSpike = t;
    emit("요청 거절(429) 급증", `최근 1분 동안 한도로 거절된 요청이 ${rise}건 늘었습니다.`);
  }

  return {
    observe(snap) {
      if (!snap || typeof snap !== "object") return;
      const list = snap.autoscale && snap.autoscale.decisions;
      if (Array.isArray(list)) decisions(list);
      const count = snap.capacity && snap.capacity.signals && snap.capacity.signals.rejected_429_1h;
      if (typeof count === "number" && isFinite(count)) rejections(count);
    },
  };
}
