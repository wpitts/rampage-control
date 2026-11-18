const state = {
  config: null,
  status: null,
  meta: { headers: [], presets: {} },
  selected: null,
  dragging: -1,
  token: sessionStorage.getItem("rampageToken") || "",
};

const $ = (id) => document.getElementById(id);

function headers() {
  const value = { "Content-Type": "application/json" };
  if (state.token) value["X-Rampage-Token"] = state.token;
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  if (response.status === 401) {
    $("auth").classList.remove("hidden");
    $("app").classList.add("hidden");
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText);
  }
  return response.json();
}

function tempSensors() {
  return (state.status?.sensors || []).filter((sensor) => sensor.kind === "temp");
}

function sensorName(id) {
  const found = (state.status?.sensors || []).find((sensor) => sensor.id === id);
  if (!found) return id || "Select a source";
  return `${found.label} (${found.chip})`;
}

function hex(color) {
  return `#${color.map((part) => part.toString(16).padStart(2, "0")).join("")}`;
}

function parseHex(value) {
  const raw = value.replace("#", "");
  return [0, 2, 4].map((offset) => parseInt(raw.slice(offset, offset + 2), 16));
}

let saveTimer = 0;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveConfig, 250);
}

async function saveConfig() {
  try {
    state.config = await api("/api/config", { method: "PUT", body: JSON.stringify(state.config) });
  } catch (error) {
    banner(String(error));
  }
}

function banner(text, sticky = false) {
  const node = document.createElement("div");
  node.className = "banner";
  if (sticky) node.dataset.notice = "1";
  node.textContent = text;
  $("banners").appendChild(node);
  if (sticky) {
    window.setTimeout(() => node.remove(), 8000);
  }
}

function renderBanners() {
  const kept = [...$("banners").querySelectorAll("[data-notice]")];
  $("banners").replaceChildren();
  (state.status?.warnings || []).forEach((text) => banner(text));
  if (state.status?.simulated) banner("Simulation is running. Nothing on a real motherboard is being changed.");
  kept.forEach((node) => $("banners").appendChild(node));
}

function selectedFan() {
  return state.config?.fans.find((fan) => fan.id === state.selected) || state.config?.fans[0] || null;
}

function renderTemps() {
  const root = $("temps");
  const sensors = (state.status?.sensors || []).filter((sensor) => sensor.kind === "temp" || sensor.kind === "voltage");
  root.replaceChildren();
  sensors.forEach((sensor) => {
    const card = document.createElement("article");
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = sensor.label;
    const value = document.createElement("div");
    value.className = "value";
    const unit = sensor.kind === "voltage" ? " V" : "°";
    value.textContent = sensor.value == null ? (sensor.state === "spun_down" ? "sleep" : "—") : `${sensor.value.toFixed(1)}${unit}`;
    const chip = document.createElement("div");
    chip.className = "muted";
    chip.textContent = sensor.chip;
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 72;
    card.append(label, value, chip, canvas);
    root.appendChild(card);
    drawSpark(canvas, state.status?.trends?.temps?.[sensor.id] || []);
  });
  const when = state.status?.updated ? new Date(state.status.updated * 1000) : null;
  $("updated").textContent = when ? `Updated ${when.toLocaleTimeString()}` : "";
}

function drawSpark(canvas, values) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  const points = values.filter((value) => value != null);
  if (points.length < 2) return;
  const min = Math.min(...points) - 1;
  const max = Math.max(...points) + 1;
  ctx.strokeStyle = "#e10600";
  ctx.lineWidth = 3;
  ctx.beginPath();
  points.forEach((value, index) => {
    const x = (index / (points.length - 1)) * (width - 8) + 4;
    const y = height - ((value - min) / (max - min)) * (height - 8) - 4;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function fillSources(select, current) {
  const currentValue = current;
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = select.id === "rgb-source" ? "Automatic" : "None";
  select.appendChild(empty);
  tempSensors().forEach((sensor) => {
    const option = document.createElement("option");
    option.value = sensor.id;
    option.textContent = sensorName(sensor.id);
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === currentValue) ? currentValue : "";
}

function renderFans() {
  const body = $("fans");
  const fans = state.config?.fans || [];
  $("fan-empty").classList.toggle("hidden", fans.length > 0);
  body.replaceChildren();
  fans.forEach((fan) => {
    const live = (state.status?.fans || []).find((item) => item.id === fan.id);
    const row = document.createElement("tr");
    if (fan.id === state.selected) row.classList.add("selected");
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, select, input")) return;
      state.selected = fan.id;
      renderFans();
      renderCurve();
    });

    const name = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.value = fan.label;
    nameInput.addEventListener("change", () => {
      fan.label = nameInput.value.trim() || fan.label;
      scheduleSave();
    });
    const hint = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Board name";
    hint.appendChild(blank);
    state.meta.headers.forEach((header) => {
      const option = document.createElement("option");
      option.value = header;
      option.textContent = header;
      hint.appendChild(option);
    });
    hint.addEventListener("change", () => {
      if (!hint.value) return;
      fan.label = hint.value;
      nameInput.value = hint.value;
      hint.value = "";
      scheduleSave();
      renderCurve();
    });
    name.append(nameInput, hint);

    const rpm = document.createElement("td");
    rpm.className = "num";
    rpm.textContent = live?.rpm == null ? "—" : Math.round(live.rpm);

    const duty = document.createElement("td");
    duty.className = "num";
    const read = live?.pwm_percent == null ? "—" : `${live.pwm_percent}%`;
    const target = live?.target_percent == null ? "" : ` → ${live.target_percent}%`;
    duty.textContent = `${read}${target}`;

    const source = document.createElement("td");
    const sourceSelect = document.createElement("select");
    fillSources(sourceSelect, fan.temp_source);
    sourceSelect.addEventListener("change", () => {
      fan.temp_source = sourceSelect.value;
      scheduleSave();
    });
    source.appendChild(sourceSelect);

    const mode = document.createElement("td");
    const modeSelect = document.createElement("select");
    ["monitor", "curve", "manual"].forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      modeSelect.appendChild(option);
    });
    modeSelect.value = fan.mode;
    modeSelect.addEventListener("change", () => {
      fan.mode = modeSelect.value;
      scheduleSave();
    });
    const enabled = document.createElement("label");
    enabled.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = fan.enabled;
    box.addEventListener("change", () => {
      fan.enabled = box.checked;
      scheduleSave();
    });
    enabled.append(box, document.createTextNode("enabled"));
    mode.append(modeSelect, enabled);

    const actions = document.createElement("td");
    const pulse = document.createElement("button");
    pulse.type = "button";
    pulse.className = "ghost";
    pulse.textContent = "Identify";
    pulse.addEventListener("click", async () => {
      try {
        await api(`/api/fans/${encodeURIComponent(fan.id)}/pulse`, { method: "POST" });
      } catch (error) {
        banner(String(error.message || error), true);
      }
    });
    actions.appendChild(pulse);
    if (live?.error) {
      const err = document.createElement("div");
      err.className = "hint";
      err.textContent = live.error;
      actions.appendChild(err);
    } else if (live?.note) {
      const note = document.createElement("div");
      note.className = "hint";
      note.textContent = live.note;
      actions.appendChild(note);
    }
    row.append(name, rpm, duty, source, mode, actions);
    body.appendChild(row);
  });
}

function renderCurve() {
  const fan = selectedFan();
  $("curve-title").textContent = fan ? fan.label || fan.id : "Curve";
  const limits = $("fan-limits");
  limits.replaceChildren();
  if (!fan) return;
  [
    ["min_percent", "Minimum %", 0, 100],
    ["max_percent", "Maximum %", 0, 100],
    ["manual_percent", "Manual %", 0, 100],
  ].forEach(([key, label, min, max]) => {
    const field = document.createElement("label");
    field.textContent = label;
    const input = document.createElement("input");
    input.type = "number";
    input.min = min;
    input.max = max;
    input.value = fan[key];
    input.addEventListener("change", () => {
      fan[key] = Number(input.value);
      scheduleSave();
    });
    field.appendChild(input);
    limits.appendChild(field);
  });
  const critical = document.createElement("label");
  critical.className = "check";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = fan.critical;
  box.addEventListener("change", () => {
    fan.critical = box.checked;
    if (fan.critical && fan.min_percent < 20) fan.min_percent = 20;
    scheduleSave();
    renderCurve();
  });
  critical.append(box, document.createTextNode("Critical header (CPU). Floor stays at 20% or higher."));
  limits.appendChild(critical);
  drawCurve();
}

function curveGeometry() {
  const canvas = $("curve");
  const rect = canvas.getBoundingClientRect();
  return { left: 42, right: canvas.width - 16, top: 16, bottom: canvas.height - 32, width: rect.width, height: rect.height };
}

function tempToX(temp, geo) {
  return geo.left + ((temp - 20) / 70) * (geo.right - geo.left);
}

function pwmToY(pwm, geo) {
  return geo.top + (1 - pwm / 100) * (geo.bottom - geo.top);
}

function xToTemp(x, geo) {
  const ratio = (x - geo.left) / (geo.right - geo.left);
  return Math.round(Math.min(95, Math.max(15, 20 + ratio * 70)));
}

function yToPwm(y, geo) {
  const ratio = (y - geo.top) / (geo.bottom - geo.top);
  return Math.round(Math.min(100, Math.max(0, (1 - ratio) * 100)));
}

function drawCurve() {
  const fan = selectedFan();
  const canvas = $("curve");
  if (!fan || !canvas) return;
  const ctx = canvas.getContext("2d");
  const geo = curveGeometry();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#2c313a";
  ctx.lineWidth = 1;
  ctx.font = "12px Consolas, monospace";
  ctx.fillStyle = "#9aa3b2";
  for (let temp = 20; temp <= 90; temp += 10) {
    const x = tempToX(temp, geo);
    ctx.beginPath();
    ctx.moveTo(x, geo.top);
    ctx.lineTo(x, geo.bottom);
    ctx.stroke();
    ctx.fillText(String(temp), x - 8, canvas.height - 12);
  }
  for (let pwm = 0; pwm <= 100; pwm += 25) {
    const y = pwmToY(pwm, geo);
    ctx.beginPath();
    ctx.moveTo(geo.left, y);
    ctx.lineTo(geo.right, y);
    ctx.stroke();
    ctx.fillText(String(pwm), 8, y + 4);
  }
  const live = (state.status?.fans || []).find((item) => item.id === fan.id);
  if (live?.temp != null) {
    const x = tempToX(live.temp, geo);
    ctx.strokeStyle = "#e6b35a";
    ctx.beginPath();
    ctx.moveTo(x, geo.top);
    ctx.lineTo(x, geo.bottom);
    ctx.stroke();
  }
  ctx.strokeStyle = "#e10600";
  ctx.lineWidth = 2;
  ctx.beginPath();
  fan.curve.forEach((point, index) => {
    const x = tempToX(point.temp, geo);
    const y = pwmToY(point.pwm, geo);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  fan.curve.forEach((point) => {
    ctx.beginPath();
    ctx.fillStyle = "#f2f4f8";
    ctx.arc(tempToX(point.temp, geo), pwmToY(point.pwm, geo), 6, 0, Math.PI * 2);
    ctx.fill();
  });
}

function eventPoint(event) {
  const canvas = $("curve");
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * (canvas.width / rect.width);
  const y = (event.clientY - rect.top) * (canvas.height / rect.height);
  return { x, y, geo: curveGeometry() };
}

function nearestPoint(x, y) {
  const fan = selectedFan();
  if (!fan) return -1;
  const geo = curveGeometry();
  let best = -1;
  let distance = 18;
  fan.curve.forEach((point, index) => {
    const dx = tempToX(point.temp, geo) - x;
    const dy = pwmToY(point.pwm, geo) - y;
    const found = Math.hypot(dx, dy);
    if (found < distance) {
      distance = found;
      best = index;
    }
  });
  return best;
}

function bindCurve() {
  const canvas = $("curve");
  canvas.addEventListener("pointerdown", (event) => {
    const point = eventPoint(event);
    state.dragging = nearestPoint(point.x, point.y);
    if (state.dragging >= 0) canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (state.dragging < 0) return;
    const fan = selectedFan();
    const point = eventPoint(event);
    const current = fan.curve[state.dragging];
    current.temp = xToTemp(point.x, point.geo);
    current.pwm = yToPwm(point.y, point.geo);
    fan.curve.sort((a, b) => a.temp - b.temp);
    state.dragging = fan.curve.indexOf(current);
    drawCurve();
  });
  canvas.addEventListener("pointerup", () => {
    if (state.dragging >= 0) scheduleSave();
    state.dragging = -1;
  });
  canvas.addEventListener("dblclick", (event) => {
    const fan = selectedFan();
    if (!fan) return;
    const point = eventPoint(event);
    fan.curve.push({ temp: xToTemp(point.x, point.geo), pwm: yToPwm(point.y, point.geo) });
    fan.curve.sort((a, b) => a.temp - b.temp);
    drawCurve();
    scheduleSave();
  });
  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    const fan = selectedFan();
    if (!fan || fan.curve.length <= 2) return;
    const point = eventPoint(event);
    const index = nearestPoint(point.x, point.y);
    if (index >= 0) {
      fan.curve.splice(index, 1);
      drawCurve();
      scheduleSave();
    }
  });
}

function zoneSourceOptions(select, current) {
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Default source";
  select.appendChild(empty);
  tempSensors().forEach((sensor) => {
    const option = document.createElement("option");
    option.value = sensor.id;
    option.textContent = sensorName(sensor.id);
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === current) ? current : "";
}

function renderZoneRow(fx, cfg) {
  const row = document.createElement("div");
  row.className = "zone-row";
  const head = document.createElement("div");
  head.className = "zone-head";
  const enable = document.createElement("input");
  enable.type = "checkbox";
  enable.title = "Control this zone";
  enable.checked = cfg ? cfg.enabled : true;
  const swatch = document.createElement("span");
  swatch.className = "swatch sm";
  const live = fx.color || [40, 40, 40];
  swatch.style.background = enable.checked ? `rgb(${live.join(",")})` : "transparent";
  const name = document.createElement("span");
  name.className = "zone-name";
  name.textContent = fx.zone || "All LEDs";
  const meta = document.createElement("span");
  meta.className = "zone-meta";
  meta.textContent = `${fx.leds} LED${fx.leds === 1 ? "" : "s"}`;
  head.append(enable, swatch, name, meta);
  row.appendChild(head);
  if (!cfg) return row;
  enable.addEventListener("change", () => {
    cfg.enabled = enable.checked;
    scheduleSave();
    renderRgb();
  });

  const controls = document.createElement("div");
  controls.className = "zone-controls grid-2";
  const mode = document.createElement("select");
  [
    ["default", "Inherit default"],
    ["temperature", "Follow temperature"],
    ["static", "Static color"],
    ["alert", "Static, flash when hot"],
    ["off", "Off"],
  ].forEach(([value, text]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    mode.appendChild(option);
  });
  mode.value = cfg.mode;
  const source = document.createElement("select");
  zoneSourceOptions(source, cfg.temp_source);
  const color = document.createElement("input");
  color.type = "color";
  color.value = hex(cfg.static_color);
  const alert = document.createElement("input");
  alert.type = "number";
  alert.min = 40;
  alert.max = 105;
  alert.step = 1;
  alert.value = cfg.alert_temp;

  mode.addEventListener("change", () => {
    cfg.mode = mode.value;
    scheduleSave();
    renderRgb();
  });
  source.addEventListener("change", () => {
    cfg.temp_source = source.value;
    scheduleSave();
  });
  color.addEventListener("input", () => {
    cfg.static_color = parseHex(color.value);
    scheduleSave();
  });
  alert.addEventListener("change", () => {
    cfg.alert_temp = Number(alert.value);
    scheduleSave();
  });

  const wrap = (text, el) => {
    const label = document.createElement("label");
    label.append(document.createTextNode(text), el);
    return label;
  };
  controls.append(wrap("Mode", mode), wrap("Source", source), wrap("Color", color), wrap("Flash °C", alert));
  const inherit = cfg.mode === "default";
  source.disabled = inherit || cfg.mode === "static" || cfg.mode === "off";
  color.disabled = inherit || cfg.mode === "temperature" || cfg.mode === "off";
  alert.disabled = inherit || !(cfg.mode === "temperature" || cfg.mode === "alert");
  if (!enable.checked) controls.classList.add("disabled");
  row.appendChild(controls);
  return row;
}

function renderRgb() {
  const rgb = state.config.rgb;
  $("rgb-mode").value = rgb.mode;
  $("rgb-enabled").checked = rgb.enabled;
  $("rgb-alert").value = rgb.alert_temp;
  $("rgb-static").value = hex(rgb.static_color);
  fillSources($("rgb-source"), rgb.temp_source);
  const gradient = $("gradient");
  gradient.replaceChildren();
  rgb.gradient.forEach((stop, index) => {
    const row = document.createElement("div");
    row.className = "grid-2";
    const temp = document.createElement("input");
    temp.type = "number";
    temp.value = stop.temp;
    temp.addEventListener("change", () => {
      stop.temp = Number(temp.value);
      scheduleSave();
    });
    const color = document.createElement("input");
    color.type = "color";
    color.value = hex(stop.color);
    color.addEventListener("input", () => {
      stop.color = parseHex(color.value);
      scheduleSave();
    });
    row.append(temp, color);
    gradient.appendChild(row);
    void index;
  });

  const zonesById = {};
  (rgb.zones || []).forEach((zone) => {
    zonesById[zone.id] = zone;
  });
  const container = $("rgb-zones");
  container.replaceChildren();
  const devices = state.status?.rgb?.devices || [];
  if (!devices.length) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "No lighting controllers detected yet.";
    container.appendChild(note);
  }
  devices.forEach((device) => {
    const group = document.createElement("div");
    group.className = "zone-group";
    const heading = document.createElement("h4");
    heading.textContent = device.name;
    group.appendChild(heading);
    (device.fixtures || []).forEach((fx) => {
      group.appendChild(renderZoneRow(fx, zonesById[fx.id]));
    });
    container.appendChild(group);
  });

  const color = state.status?.rgb?.color || [0, 0, 0];
  $("rgb-swatch").style.background = `rgb(${color.join(",")})`;
  $("rgb-error").textContent = state.status?.rgb?.error || (state.status?.rgb?.connected ? "Lighting link is up." : "");
}

function renderScalars() {
  $("arm").checked = !!state.config.armed;
  $("poll").value = state.config.poll_seconds;
  $("hysteresis").value = state.config.hysteresis_c;
  $("slew").value = state.config.slew_percent_per_sec;
  const found = (state.status?.sensors || []).some((sensor) => String(sensor.chip).toLowerCase().includes("nct"));
  $("chip-pill").textContent = state.status?.simulated ? "Simulated NCT6791D" : found ? "NCT6791D visible" : "NCT6791D not found";
}

function render() {
  if (!state.config || !state.status) return;
  $("app").classList.remove("hidden");
  $("auth").classList.add("hidden");
  if (!state.selected && state.config.fans[0]) state.selected = state.config.fans[0].id;
  renderBanners();
  renderTemps();
  renderFans();
  renderCurve();
  renderRgb();
  renderScalars();
}

function refreshLive() {
  if (state.dragging >= 0) {
    drawCurve();
    return;
  }
  renderBanners();
  renderTemps();
  document.querySelectorAll("#fans tr").forEach((row, index) => {
    const fan = state.config.fans[index];
    const live = (state.status.fans || []).find((item) => item.id === fan?.id);
    const cells = row.children;
    if (!live || cells.length < 3) return;
    cells[1].textContent = live.rpm == null ? "—" : Math.round(live.rpm);
    const read = live.pwm_percent == null ? "—" : `${live.pwm_percent}%`;
    const target = live.target_percent == null ? "" : ` → ${live.target_percent}%`;
    cells[2].textContent = `${read}${target}`;
  });
  drawCurve();
  const color = state.status?.rgb?.color || [0, 0, 0];
  $("rgb-swatch").style.background = `rgb(${color.join(",")})`;
  $("rgb-error").textContent = state.status?.rgb?.error || "";
  const when = state.status?.updated ? new Date(state.status.updated * 1000) : null;
  $("updated").textContent = when ? `Updated ${when.toLocaleTimeString()}` : "";
}

function bindSettings() {
  $("arm").addEventListener("change", async () => {
    const armed = $("arm").checked;
    if (armed && !confirm("Arm fan control? Headers set to curve or manual will change speed.")) {
      $("arm").checked = false;
      return;
    }
    try {
      state.config = await api("/api/armed", { method: "POST", body: JSON.stringify({ armed }) });
    } catch (error) {
      banner(String(error.message || error));
      $("arm").checked = !!state.config.armed;
    }
  });
  $("prepare").addEventListener("click", () => {
    const preset = state.meta.presets.balanced;
    const packageSensor = tempSensors().find((sensor) => /package/i.test(sensor.label));
    state.config.fans.forEach((fan) => {
      fan.mode = "curve";
      fan.enabled = true;
      fan.min_percent = preset.min_percent;
      fan.max_percent = preset.max_percent;
      fan.curve = preset.curve.map(([temp, pwm]) => ({ temp, pwm }));
      if (packageSensor) fan.temp_source = packageSensor.id;
    });
    scheduleSave();
    render();
  });
  $("preset").addEventListener("change", () => {
    const fan = selectedFan();
    const preset = state.meta.presets[$("preset").value];
    if (!fan || !preset) return;
    fan.min_percent = preset.min_percent;
    fan.max_percent = preset.max_percent;
    fan.curve = preset.curve.map(([temp, pwm]) => ({ temp, pwm }));
    if (fan.critical && fan.min_percent < 20) fan.min_percent = 20;
    $("preset").value = "";
    scheduleSave();
    renderCurve();
  });
  ["rgb-mode", "rgb-source", "rgb-static", "rgb-alert", "rgb-enabled", "poll", "hysteresis", "slew"].forEach((id) => {
    $(id).addEventListener("change", () => {
      const rgb = state.config.rgb;
      rgb.mode = $("rgb-mode").value;
      rgb.temp_source = $("rgb-source").value;
      rgb.static_color = parseHex($("rgb-static").value);
      rgb.alert_temp = Number($("rgb-alert").value);
      rgb.enabled = $("rgb-enabled").checked;
      state.config.poll_seconds = Number($("poll").value);
      state.config.hysteresis_c = Number($("hysteresis").value);
      state.config.slew_percent_per_sec = Number($("slew").value);
      scheduleSave();
      renderRgb();
    });
  });
}

let controlsReady = false;

async function loadDashboard() {
  const [config, status, meta] = await Promise.all([api("/api/config"), api("/api/status"), api("/api/meta")]);
  state.config = config;
  state.status = status;
  state.meta = meta;
  if (!controlsReady) {
    Object.entries(meta.presets).forEach(([name]) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name[0].toUpperCase() + name.slice(1);
      $("preset").appendChild(option);
    });
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Apply preset";
    $("preset").prepend(placeholder);
    $("preset").value = "";
    bindCurve();
    bindSettings();
    controlsReady = true;
    window.setInterval(async () => {
      try {
        const previous = (state.status?.fans || []).map((fan) => fan.id).join();
        const previousDevices = (state.status?.rgb?.devices || []).length;
        state.status = await api("/api/status");
        const next = (state.status.fans || []).map((fan) => fan.id).join();
        const configIds = (state.config.fans || []).map((fan) => fan.id).join();
        const devices = (state.status.rgb?.devices || []).length;
        if (previous !== next || configIds !== next || previousDevices !== devices) {
          state.config = await api("/api/config");
          render();
        } else {
          refreshLive();
        }
      } catch (_error) {
        /* the next poll retries */
      }
    }, 2000);
  }
  render();
}

$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("token").value.trim();
  sessionStorage.setItem("rampageToken", state.token);
  try {
    await loadDashboard();
  } catch (error) {
    if (String(error.message || error) !== "unauthorized") banner(String(error.message || error));
  }
});

loadDashboard().catch((error) => {
  if (String(error.message || error) !== "unauthorized") banner(String(error.message || error));
});
