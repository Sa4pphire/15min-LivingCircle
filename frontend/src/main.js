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
      <div class="map-placeholder">
        <div>
          <strong>地图区域</strong>
          <p>下一步接入百度 JSAPI、地点搜索和分析图层。</p>
        </div>
      </div>
    </section>
    <aside class="report-panel">
      <p class="eyebrow">ANALYSIS REPORT</p>
      <h2>社区体检报告</h2>
      <div class="status-card">
        <span>服务状态</span>
        <strong id="service-status">尚未检查</strong>
      </div>
      <div class="metric-grid">
        <article><span>等时圈</span><strong>--</strong></article>
        <article><span>设施覆盖</span><strong>--</strong></article>
        <article><span>服务盲区</span><strong>--</strong></article>
        <article><span>综合得分</span><strong>--</strong></article>
      </div>
      <p class="hint">当前为仓库骨架页面，真实分析流程将在后续里程碑接入。</p>
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
