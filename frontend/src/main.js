import "./styles.css";

const app = document.querySelector("#app");

app.innerHTML = `
  <main class="app-shell">
    <section class="map-panel">
      <header class="toolbar">
        <div>
          <p class="eyebrow">OPEN MAP ANALYSIS</p>
          <h1>15 分钟生活圈体检助手</h1>
        </div>
        <button id="health-check" type="button">检查服务</button>
      </header>
      <form id="analysis-form" class="analysis-form">
        <label>中心点经度 <input id="center-lng" type="number" step="any" value="121.5" required /></label>
        <label>中心点纬度 <input id="center-lat" type="number" step="any" value="31.3" required /></label>
        <button type="submit">计算 15 分钟步行范围</button>
      </form>
      <div class="map-placeholder" id="map-region">
        <svg id="network-view" viewBox="0 0 1000 600" role="img" aria-label="可达街段与近似等时圈示意图" hidden></svg>
        <div id="map-empty">
          <strong>步行路网示意图</strong>
          <p>分析完成后显示可达街段和近似等时圈。</p>
        </div>
      </div>
      <p class="map-legend">蓝绿色：可达人行道　青色：可达共享通道　橙色：可达过街连接　浅绿色：近似展示范围</p>
    </section>
    <aside class="report-panel">
      <p class="eyebrow">ANALYSIS REPORT</p>
      <h2>社区体检报告</h2>
      <div class="status-card">
        <span>服务状态</span>
        <strong id="service-status">尚未检查</strong>
      </div>
      <p id="analysis-status" class="hint" role="status" aria-live="polite">尚未开始分析。</p>
      <div class="metric-grid">
        <article><span>可达路网节点</span><strong id="reachable-nodes">--</strong></article>
        <article><span>可达过街连接</span><strong id="reachable-crossings">--</strong></article>
        <article><span>可达街段</span><strong id="reachable-edges">--</strong></article>
        <article><span>15 分钟内设施</span><strong id="reachable-facilities">--</strong></article>
        <article><span>路网来源</span><strong id="network-source">--</strong></article>
      </div>
      <p id="analysis-note" class="hint">等时圈面仅供展示；设施可达性以路网步行耗时为准。</p>
    </aside>
  </main>
`;

const healthButton = document.querySelector("#health-check");
const serviceStatus = document.querySelector("#service-status");

healthButton.addEventListener("click", async () => {
  serviceStatus.textContent = "检查中…";
  try {
    const response = await fetch("/api/v1/health");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    serviceStatus.textContent = `${payload.status} / engine ${payload.engine.status}`;
  } catch (error) {
    serviceStatus.textContent = `不可用：${error.message}`;
  }
});

const analysisForm = document.querySelector("#analysis-form");
const analysisStatus = document.querySelector("#analysis-status");
const analysisNote = document.querySelector("#analysis-note");
const networkView = document.querySelector("#network-view");
const mapEmpty = document.querySelector("#map-empty");

function renderNetwork(result) {
  const polygonRings = result.isochrone?.geometry?.coordinates?.flat() ?? [];
  const features = result.reachableWalkways?.features ?? [];
  const points = [
    ...polygonRings.flat(),
    ...features.flatMap((feature) => feature.geometry?.coordinates ?? []),
  ].filter((point) => Array.isArray(point) && point.length === 2 &&
    point.every(Number.isFinite));
  if (points.length === 0) throw new Error("结果中没有可绘制的路网坐标");

  const latitudeScale = Math.cos(points[0][1] * Math.PI / 180);
  const east = points.map((point) => point[0] * latitudeScale);
  const north = points.map((point) => point[1]);
  const minX = Math.min(...east);
  const maxX = Math.max(...east);
  const minY = Math.min(...north);
  const maxY = Math.max(...north);
  const scale = Math.min(900 / Math.max(maxX - minX, 1e-8),
                         500 / Math.max(maxY - minY, 1e-8));
  const offsetX = (1000 - (maxX - minX) * scale) / 2;
  const offsetY = (600 - (maxY - minY) * scale) / 2;
  const drawPoint = (point) => [
    offsetX + (point[0] * latitudeScale - minX) * scale,
    600 - offsetY - (point[1] - minY) * scale,
  ].join(",");
  const ns = "http://www.w3.org/2000/svg";
  networkView.replaceChildren();
  for (const polygonRingsForArea of result.isochrone?.geometry?.coordinates ?? []) {
    const pathData = polygonRingsForArea.filter((ring) => ring.length >= 4)
      .map((ring) => `M ${ring.map(drawPoint).join(" L ")} Z`).join(" ");
    if (!pathData) continue;
    const polygon = document.createElementNS(ns, "path");
    polygon.setAttribute("d", pathData);
    polygon.setAttribute("fill-rule", "evenodd");
    polygon.setAttribute("class", "reachable-area");
    networkView.append(polygon);
  }
  for (const feature of features) {
    const path = feature.geometry?.coordinates;
    if (!Array.isArray(path) || path.length < 2) continue;
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", path.map(drawPoint).join(" "));
    line.setAttribute("class", feature.properties?.kind === "crossing"
      ? "reachable-crossing" : feature.properties?.kind === "shared_way"
        ? "reachable-shared-way" : "reachable-walkway");
    networkView.append(line);
  }
  networkView.hidden = false;
  mapEmpty.hidden = true;
}

analysisForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = analysisForm.querySelector("button");
  button.disabled = true;
  analysisStatus.textContent = "正在检查步行路网…";
  try {
    const lng = Number(document.querySelector("#center-lng").value);
    const lat = Number(document.querySelector("#center-lat").value);
    const created = await fetch("/api/v1/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ center: { lng, lat, coordType: "bd09ll" }, minutes: 15 }),
    });
    const accepted = await created.json();
    if (!created.ok) {
      throw new Error(accepted.detail?.message ?? `HTTP ${created.status}`);
    }
    let completed;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const response = await fetch(`/api/v1/analyses/${accepted.analysisId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      if (state.status === "completed") {
        completed = state.result;
        break;
      }
      if (state.status === "failed") throw new Error(state.error ?? "分析失败");
      analysisStatus.textContent = `计算中：${state.progress?.stage ?? state.status}`;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!completed) throw new Error("分析超时，请稍后重试");
    renderNetwork(completed);
    document.querySelector("#reachable-nodes").textContent =
      String(completed.metrics.reachableNodeCount);
    document.querySelector("#reachable-crossings").textContent =
      String(completed.metrics.reachableCrossingCount);
    document.querySelector("#reachable-edges").textContent =
      String(completed.reachableWalkways.features.length);
    document.querySelector("#reachable-facilities").textContent =
      `${completed.metrics.reachableFacilityCount}/${completed.metrics.facilityCount}`;
    document.querySelector("#network-source").textContent =
      completed.metadata.networkSource === "synthetic" ? "合成样例" : "人工标注";
    analysisNote.textContent = completed.metadata.networkSource === "synthetic"
      ? "当前为合成测试路网，不代表真实街道；展示面仅供示意。"
      : "等时圈面仅供展示；设施可达性以路网步行耗时为准。";
    analysisStatus.textContent = "分析完成";
  } catch (error) {
    analysisStatus.textContent = `无法完成分析：${error.message}`;
  } finally {
    button.disabled = false;
  }
});
