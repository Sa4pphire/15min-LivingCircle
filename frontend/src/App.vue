<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import RealMapStage from "./RealMapStage.vue";
import PoiInventoryPanel from "./PoiInventoryPanel.vue";
import { requestMapAnalysis } from "./analysisClient.js";
import { requestCppMapAnalysis } from "./cppAnalysisClient.js";
import { createMapWheelStepper, mapZoomTiers } from "./mapZoom";

const pageViewport = ref(null);
const detailPage = ref(null);
const wordmarkEl = ref(null);
const wordmarkSvg = ref(null);
const wordmarkText = ref(null);
const cometLayer = ref(null);
const activePage = ref(0);
// Always show the wordmark on a fresh visit; this is not a saved preference.
const livingFooterVisible = ref(true);
const livingFooterAnimating = ref(false);
let livingFooterTimer;
const requestedMode = new URLSearchParams(window.location.search).get("mode");
const mapMode = ref(requestedMode === "synthetic" ? "synthetic" : "real");
const mapModes = [
  { id: "real", label: "快速模式", description: "固定圆与临时路线，仅作交互示意" },
  { id: "synthetic", label: "专家模式", description: "C++ 路网等时圈，当前仍使用合成数据" },
];
const mapZoomTier = ref("medium");
const mapOverviewRequestId = ref(0);
const realCandidate = ref(null);
const realAnalysisResult = ref(null);
const realAnalysisState = ref("idle");
const realAnalysisError = ref("");
let realAnalysisAbort;
let realAnimationTimer;
const cppCandidate = ref(null);
const cppAnalysisResult = ref(null);
const cppAnalysisState = ref("idle");
const cppAnalysisError = ref("");
const cppPoiCategory = ref("all");
let cppAnalysisAbort;
let cppAnimationTimer;
let wordmarkFrame;
let cometPoints = [];
let cometParticles = [];
let cometStreaks = [];
const cometLifetime = 620;
const cometParticleCount = 24;
const livingLetters = Array.from("LIVING CIRCLE");
let pageWheelDistance = 0;
let pageTurnTimer;
let pageTurning = false;
const mapWheelStepper = createMapWheelStepper();

function toggleLivingFooter() {
  clearTimeout(livingFooterTimer);
  livingFooterAnimating.value = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  livingFooterVisible.value = !livingFooterVisible.value;
  // CSS reverses from its current height on repeated clicks. Keep a fallback
  // for background tabs or browsers that do not dispatch transitionend.
  if (livingFooterAnimating.value) {
    livingFooterTimer = setTimeout(finishLivingFooterTransition, 480);
  }
}

function finishLivingFooterTransition(event) {
  if (event && (event.target !== event.currentTarget || event.propertyName !== "height")) return;
  clearTimeout(livingFooterTimer);
  livingFooterAnimating.value = false;
}

function chooseRealPoint(point) {
  realAnalysisAbort?.abort();
  clearTimeout(realAnimationTimer);
  realCandidate.value = point;
  realAnalysisResult.value = null;
  realAnalysisState.value = "idle";
  realAnalysisError.value = "";
}

async function runRealAnalysis() {
  if (!realCandidate.value?.local || realAnalysisState.value === "running") return;
  realAnalysisAbort?.abort();
  const controller = new AbortController();
  realAnalysisAbort = controller;
  realAnalysisResult.value = null;
  realAnalysisError.value = "";
  realAnalysisState.value = "running";
  try {
    const result = await requestMapAnalysis({ origin: realCandidate.value.local }, { signal: controller.signal });
    if (controller.signal.aborted) return;
    realAnalysisResult.value = result;
    realAnimationTimer = setTimeout(() => {
      if (!controller.signal.aborted) realAnalysisState.value = "complete";
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 3200);
  } catch (error) {
    if (controller.signal.aborted) return;
    realAnalysisState.value = "error";
    realAnalysisError.value = "示意路线暂时无法生成，请重新选点后重试。";
    console.warn("真实区域合成分析失败。", error);
  }
}

function chooseCppPoint(point) {
  if (cppAnalysisState.value === "running") return;
  cppAnalysisAbort?.abort();
  clearTimeout(cppAnimationTimer);
  cppCandidate.value = point;
  cppAnalysisResult.value = null;
  if (mapZoomTier.value === "result") mapZoomTier.value = "medium";
  cppAnalysisState.value = "idle";
  cppAnalysisError.value = "";
}

async function runCppAnalysis(refreshPois = false) {
  if (!cppCandidate.value?.local || cppAnalysisState.value === "running") return;
  cppAnalysisAbort?.abort();
  clearTimeout(cppAnimationTimer);
  const controller = new AbortController();
  cppAnalysisAbort = controller;
  cppAnalysisResult.value = null;
  cppAnalysisError.value = "";
  cppAnalysisState.value = "running";
  try {
    const result = await requestCppMapAnalysis({ origin: cppCandidate.value.local,
      includePois: true, refreshPois: refreshPois === true },
      { signal: controller.signal });
    if (controller.signal.aborted) return;
    cppAnalysisResult.value = result;
    cppAnimationTimer = setTimeout(() => {
      if (!controller.signal.aborted) cppAnalysisState.value = "complete";
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 3200);
  } catch (error) {
    if (controller.signal.aborted) return;
    cppAnalysisState.value = "error";
    cppAnalysisError.value = String(error?.message).includes("ORIGIN_NOT_ON_WALKWAY")
      ? "到最近合成路段已耗尽步行预算，请在道路附近重新选点。"
      : "C++ 分析未完成，请确认 Python 后端和 C++ 引擎已启动。";
    console.warn("合成路网 C++ 分析失败。", error);
  }
}

function switchMapMode(mode) {
  if (!mapModes.some(item => item.id === mode) || mapMode.value === mode) return;
  mapWheelStepper.reset();
  if (mapZoomTier.value === "result") mapZoomTier.value = "medium";
  mapMode.value = mode;
}

function setMapZoomTier(tier, source = "button") {
  if (source !== "wheel") mapWheelStepper.reset();
  // Refitting a result is an explicit user action, including another click
  // while already in overview mode. Analysis completion never changes it.
  if (tier === "result") mapOverviewRequestId.value += 1;
  if (mapZoomTier.value === tier) return;
  mapZoomTier.value = tier;
}

function handleMapWheel(event) {
  // Capture before the SDK: even at a zoom limit, never chain to page turning.
  event.stopPropagation();
  pageWheelDistance = 0;
  if (event.ctrlKey) {
    mapWheelStepper.reset();
    return; // Preserve browser zoom / trackpad pinch-to-zoom.
  }
  event.preventDefault();
  if (pageTurning) return;
  const tier = mapWheelStepper.step(mapZoomTier.value, event, performance.now(), window.innerHeight);
  if (tier) setMapZoomTier(tier, "wheel");
}

function motionEnabled(event) {
  return event.pointerType === "mouse"
    && window.matchMedia("(hover: hover) and (pointer: fine)").matches
    && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function fitWordmark() {
  if (!wordmarkSvg.value || !wordmarkText.value || !wordmarkEl.value) return;
  const bounds = wordmarkText.value.getBBox();
  if (!bounds.width || !bounds.height) return;
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return;
  context.font = getComputedStyle(wordmarkText.value).font;
  const metrics = context.measureText("GEOVIEW");
  const ascent = metrics.actualBoundingBoxAscent || 146;
  const descent = metrics.actualBoundingBoxDescent || 0;
  const inset = 3;
  const width = bounds.width + inset * 2;
  const height = ascent + descent + inset * 2;
  wordmarkSvg.value.setAttribute("viewBox", `${bounds.x - inset} ${160 - ascent - inset} ${width} ${height}`);
  wordmarkEl.value.style.aspectRatio = `${width} / ${height}`;
}

function ensureCometParticles() {
  if (!cometLayer.value || cometParticles.length) return;
  for (let index = 0; index < cometParticleCount - 1; index += 1) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("stroke", "#74d7b6");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("opacity", "0");
    cometLayer.value.appendChild(line);
    cometStreaks.push(line);
  }
  for (let index = 0; index < cometParticleCount; index += 1) {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("fill", "url(#geoview-comet-gradient)");
    circle.setAttribute("opacity", "0");
    cometLayer.value.appendChild(circle);
    cometParticles.push(circle);
  }
}

function paintComet(now) {
  cometPoints = cometPoints.filter((point) => now - point.time < cometLifetime);
  cometStreaks.forEach((line, index) => {
    const head = cometPoints[cometPoints.length - 1 - index];
    const tail = cometPoints[cometPoints.length - 2 - index];
    if (!head || !tail) {
      line.setAttribute("opacity", "0");
      return;
    }
    const strength = Math.max(0, 1 - (now - head.time) / cometLifetime);
    const taper = 1 - index / cometParticleCount;
    line.setAttribute("x1", tail.x);
    line.setAttribute("y1", tail.y);
    line.setAttribute("x2", head.x);
    line.setAttribute("y2", head.y);
    line.setAttribute("stroke-width", `${(2 + strength * 8) * taper}`);
    line.setAttribute("opacity", `${strength * taper * .68}`);
  });
  cometParticles.forEach((circle, index) => {
    const point = cometPoints[cometPoints.length - 1 - index];
    if (!point) {
      circle.setAttribute("opacity", "0");
      return;
    }
    const strength = Math.max(0, 1 - (now - point.time) / cometLifetime);
    circle.setAttribute("cx", point.x);
    circle.setAttribute("cy", point.y);
    circle.setAttribute("r", `${12 + strength * 24}`);
    circle.setAttribute("opacity", `${Math.pow(strength, 1.5) * (1 - index / cometParticleCount)}`);
  });
  wordmarkFrame = cometPoints.length ? requestAnimationFrame(paintComet) : 0;
}

function moveWordmark(event) {
  if (!motionEnabled(event) || !wordmarkSvg.value || !event.target?.classList?.contains("geoview-hit")) return;
  ensureCometParticles();
  const matrix = wordmarkSvg.value.getScreenCTM();
  if (!matrix) return;
  const cursor = wordmarkSvg.value.createSVGPoint();
  cursor.x = event.clientX;
  cursor.y = event.clientY;
  const point = cursor.matrixTransform(matrix.inverse());
  const now = performance.now();
  const previous = cometPoints[cometPoints.length - 1];
  const distance = previous ? Math.hypot(point.x - previous.x, point.y - previous.y) : 0;
  const steps = previous ? Math.min(12, Math.max(1, Math.ceil(distance / 13))) : 1;
  for (let index = 1; index <= steps; index += 1) {
    const fraction = index / steps;
    cometPoints.push({
      x: previous ? previous.x + (point.x - previous.x) * fraction : point.x,
      y: previous ? previous.y + (point.y - previous.y) * fraction : point.y,
      time: now - (steps - index) * 6,
    });
  }
  if (cometPoints.length > cometParticleCount) cometPoints.splice(0, cometPoints.length - cometParticleCount);
  if (!wordmarkFrame) wordmarkFrame = requestAnimationFrame(paintComet);
}

function goToPage(index) {
  const targetPage = Math.min(1, Math.max(0, index));
  mapWheelStepper.reset();
  if (!pageViewport.value) return;
  pageWheelDistance = 0;
  if (targetPage === activePage.value && Math.abs(pageViewport.value.scrollTop - targetPage * pageViewport.value.clientHeight) < 2) return;
  pageTurning = true;
  activePage.value = targetPage;
  clearTimeout(pageTurnTimer);
  pageViewport.value.scrollTo({
    top: targetPage === 0 ? 0 : detailPage.value?.offsetTop ?? pageViewport.value.clientHeight,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  });
  pageTurnTimer = setTimeout(() => { pageTurning = false; }, 720);
}

function syncPage() {
  if (!pageViewport.value) return;
  activePage.value = pageViewport.value.scrollTop >= pageViewport.value.clientHeight / 2 ? 1 : 0;
}

function handlePageWheel(event) {
  if (event.defaultPrevented || event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
  if (!delta) return;
  const scroller = event.target instanceof Element
    ? event.target.closest(".detail-scroll") : null;
  if (scroller) {
    const remaining = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop;
    if ((delta > 0 && remaining > 2) || (delta < 0 && scroller.scrollTop > 2)) {
      pageWheelDistance = 0;
      return;
    }
  }
  event.preventDefault();
  if (pageTurning) return;
  if (Math.sign(delta) !== Math.sign(pageWheelDistance)) pageWheelDistance = 0;
  pageWheelDistance += delta;
  if (Math.abs(pageWheelDistance) < 150) return;
  goToPage(activePage.value + Math.sign(pageWheelDistance));
}

function handlePageKeydown(event) {
  if (event.target instanceof Element && event.target.closest(".detail-scroll, input, select, textarea")) return;
  if (event.key === "PageDown" || event.key === "ArrowDown") {
    event.preventDefault();
    goToPage(1);
  } else if (event.key === "PageUp" || event.key === "ArrowUp") {
    event.preventDefault();
    goToPage(0);
  }
}

onMounted(() => {
  document.fonts.ready.then(fitWordmark);
});

onUnmounted(() => {
  realAnalysisAbort?.abort();
  clearTimeout(realAnimationTimer);
  cppAnalysisAbort?.abort();
  clearTimeout(cppAnimationTimer);
  if (wordmarkFrame) cancelAnimationFrame(wordmarkFrame);
  clearTimeout(pageTurnTimer);
  clearTimeout(livingFooterTimer);
});
</script>

<template>
  <div ref="pageViewport" class="app-shell" @wheel="handlePageWheel" @scroll.passive="syncPage" @keydown="handlePageKeydown">
    <section class="book-page map-page" aria-label="地图演示页">
    <header class="app-header">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 36 36" fill="none">
            <path d="M18 4.5c7.5 0 13.5 6 13.5 13.5S25.5 31.5 18 31.5 4.5 25.5 4.5 18 10.5 4.5 18 4.5Z" stroke="currentColor" stroke-width="1.5" />
            <path d="M18 9.5c4.7 0 8.5 3.8 8.5 8.5s-3.8 8.5-8.5 8.5-8.5-3.8-8.5-8.5 3.8-8.5 8.5-8.5Z" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3" />
            <circle cx="18" cy="18" r="3.2" fill="currentColor" />
          </svg>
        </div>
        <div>
          <h1 class="brand-title">步行生活圈</h1>
          <div class="brand-subtitle">15 分钟可达性体检 · 演示版</div>
        </div>
      </div>
      <div class="header-message">
        <strong>从一个起点，看见城市的日常半径。</strong>
        <span>上海 · 新江湾城样例区</span>
      </div>
      <div class="header-right">
        <button type="button" class="header-nav-button living-footer-toggle"
          aria-controls="living-circle-footer" :aria-expanded="livingFooterVisible"
          :aria-label="livingFooterVisible ? '隐藏 LIVING CIRCLE 区域' : '显示 LIVING CIRCLE 区域'"
          :title="livingFooterVisible ? '隐藏 LIVING CIRCLE 区域，让地图更大' : '显示 LIVING CIRCLE 区域'"
          @click="toggleLivingFooter">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6S2.5 12 2.5 12Z" />
            <circle cx="12" cy="12" r="2.5" />
            <path v-if="!livingFooterVisible" d="m4 4 16 16" />
          </svg>
          <span class="living-toggle-label">{{ livingFooterVisible ? '隐藏字样' : '显示字样' }}</span>
        </button>
        <button type="button" class="header-nav-button" @click="goToPage(1)">查看报告 <span aria-hidden="true">↓</span></button>
        <a class="header-contact" href="https://github.com/Sa4pphire" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 联系项目作者">联系 <span class="github-label">/ GitHub</span> <span aria-hidden="true">↗</span></a>
      </div>
    </header>

    <main class="dashboard">
      <section class="map-column" aria-label="生活圈地图">
        <div class="map-frame" @wheel.capture="handleMapWheel">
          <div class="map-topline">
            <div class="map-head-left">
              <span class="map-title"><span class="map-title-mark"></span>新江湾城 · 四路围合演示区 <small>{{ mapMode === "real" ? "快速模式 · 示意路线" : "专家模式 · C++ 等时圈" }}</small></span>
            </div>
            <button v-if="mapMode === 'real'" type="button" class="analyze-button map-analyze-button real-mode-action" :class="{ 'is-running': realAnalysisState === 'running', 'is-complete': realAnalysisState === 'complete' }" :disabled="!realCandidate || realAnalysisState === 'running'" @click="runRealAnalysis">
              <span class="button-label">{{ !realCandidate ? '先在地图选点' : realAnalysisState === 'running' ? '正在绘制路线…' : realAnalysisState === 'complete' ? '重新生成示意' : '生成示意分析' }}</span><span class="button-arrow" aria-hidden="true">{{ realAnalysisState === 'complete' ? '✓' : realAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
            <button v-else-if="mapMode === 'synthetic'" type="button" class="analyze-button map-analyze-button real-mode-action"
              :class="{ 'is-running': cppAnalysisState === 'running', 'is-complete': cppAnalysisState === 'complete' }"
              :disabled="!cppCandidate || cppAnalysisState === 'running'" @click="runCppAnalysis">
              <span class="button-label">{{ !cppCandidate ? '先在地图选点' : cppAnalysisState === 'running' ? 'C++ 计算中…' : cppAnalysisState === 'complete' ? '重新计算等时圈' : '计算 15 分钟等时圈' }}</span>
              <span class="button-arrow" aria-hidden="true">{{ cppAnalysisState === 'complete' ? '✓' : cppAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
          </div>

          <div class="map-canvas">
            <Transition name="map-mode">
              <RealMapStage
                :analysis-mode="mapMode === 'synthetic' ? 'cpp' : 'preview'"
                :candidate="mapMode === 'synthetic' ? cppCandidate : realCandidate"
                :analysis-result="mapMode === 'synthetic' ? cppAnalysisResult : realAnalysisResult"
                :zoom-tier="mapZoomTier" :overview-request-id="mapOverviewRequestId"
                :layout-transitioning="livingFooterAnimating"
                :selection-disabled="mapMode === 'synthetic' && cppAnalysisState === 'running'"
                @select="mapMode === 'synthetic' ? chooseCppPoint($event) : chooseRealPoint($event)" />
            </Transition>
            <!-- Replay a light reveal without remounting the shared base map. -->
            <div :key="mapMode" class="map-mode-wash" aria-hidden="true"></div>
            <div class="map-mode-switch" role="group" aria-label="地图展示模式"
              :style="{ '--mode-index': mapModes.findIndex(mode => mode.id === mapMode), '--mode-count': mapModes.length }"
              @pointerdown.stop @click.stop @dblclick.stop>
              <span class="map-mode-indicator" aria-hidden="true"></span>
              <button v-for="mode in mapModes" :key="mode.id" type="button" :title="mode.description"
                :aria-pressed="mapMode === mode.id" :class="{ active: mapMode === mode.id }"
                @click="switchMapMode(mode.id)">{{ mode.label }}</button>
            </div>
            <div class="map-compass" aria-hidden="true"><span>北</span><i></i></div>
            <div class="map-zoom-control" role="group" aria-label="地图比例尺"
              title="地图上滚轮向上放大，向下缩小（大／中／小三档）">
              <span class="map-zoom-heading" aria-hidden="true">比例尺</span>
              <button
                v-for="tier in mapZoomTiers"
                :key="tier.id"
                type="button"
                :title="tier.description"
                :aria-label="tier.description"
                :aria-pressed="mapZoomTier === tier.id"
                :class="{ active: mapZoomTier === tier.id }"
                @click="setMapZoomTier(tier.id)"
              ><strong>{{ tier.label }}</strong><small>{{ tier.hint }}</small></button>
              <button v-if="mapMode === 'synthetic' && cppAnalysisResult" type="button"
                aria-label="显示全圈及圈内 POI" :aria-pressed="mapZoomTier === 'result'"
                :class="{ active: mapZoomTier === 'result' }" @click="setMapZoomTier('result')">
                <strong>全圈</strong><small>适配</small>
              </button>
              <span class="map-zoom-hint" aria-hidden="true">{{ ['small', 'result'].includes(mapZoomTier) ? '固定' : '可拖动' }}</span>
            </div>
          </div>

          <div class="map-bottomline real-mode">
            <span v-if="mapMode === 'real'"><span class="line-signal"></span>{{ realAnalysisState === 'error' ? realAnalysisError : realAnalysisState === 'running' ? '正在生成合成路线，请稍候' : realAnalysisResult ? '固定圆与临时路线已显示 · 非真实等时圈' : realCandidate ? '已选起点 · 点击右上角生成示意' : '四路围合范围 · 点击地图选点' }}</span>
            <span v-else-if="mapMode === 'synthetic'"><span class="line-signal"></span>{{ cppAnalysisState === 'error' ? cppAnalysisError : cppAnalysisState === 'running' ? 'Python → C++ 正在计算 15 分钟路网等时圈' : cppAnalysisResult ? 'C++ 等时圈与可达街段已显示 · 路网仍为合成数据' : cppCandidate ? '已选起点 · 点击右上角计算等时圈' : '专家模式 · 合成路网，点击地图选点' }}</span>
            <span><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">边界与 SVG 数据 © OpenStreetMap contributors · ODbL</a></span>
          </div>
        </div>
      </section>
    </main>
    <footer id="living-circle-footer" class="living-footer"
      :class="{ 'is-hidden': !livingFooterVisible }" :aria-hidden="!livingFooterVisible" aria-label="LIVING CIRCLE"
      @transitionend="finishLivingFooterTransition">
      <div class="living-footer-content">
        <div class="living-meta" aria-hidden="true">
          <span>{{ mapMode === 'real' ? 'REAL AREA PREVIEW / XINJIANGWANCHENG' : 'C++ ISOCHRONE / SYNTHETIC ROAD GRAPH' }}</span>
          <span>向下滚动 · 查看生活圈报告 ↓</span>
        </div>
        <div class="living-wordmark" aria-hidden="true">
          <div class="living-wordmark-line">
            <span v-for="(letter, index) in livingLetters" :key="index" class="living-character" :class="{ 'is-space': letter === ' ' }" :style="{ '--letter-index': index }">
              {{ letter === ' ' ? '\u00a0' : letter }}
              <template v-if="letter !== ' '">
                <span class="living-slice living-slice-top">{{ letter }}</span>
                <span class="living-slice living-slice-middle">{{ letter }}</span>
                <span class="living-slice living-slice-bottom">{{ letter }}</span>
              </template>
            </span>
          </div>
        </div>
      </div>
    </footer>
    </section>

    <section ref="detailPage" class="book-page detail-page" aria-label="生活圈控制面板">
      <div class="detail-page-inner">
        <div class="detail-page-heading">
          <div>
            <p class="section-kicker">{{ mapMode === 'real' ? '快速模式 · 新江湾城演示范围' : '专家模式 · C++ 算法演示' }}</p>
            <h2>{{ mapMode === 'real' ? '真实范围与选点状态' : '从地图走进路网计算' }}</h2>
          </div>
          <button type="button" class="return-map-button" @click="goToPage(0)">返回地图 <span aria-hidden="true">↑</span></button>
        </div>
      <div class="detail-scroll">

      <aside v-if="mapMode === 'real'" class="real-insight-panel" aria-label="快速模式预览信息">
        <div class="demo-warning real-data-warning">
          <span class="warning-icon">!</span>
          <span><strong>真实区域上的合成示意，不是分析报告</strong> · 点击分析后展示固定半径圆与临时路线。道路类型、过街和设施入口均未经核查。</span>
        </div>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">01</span><h2>演示区域</h2></div>
          <p class="section-explain">这是项目自定义的道路围合区域，不是新江湾城街道行政边界。</p>
          <div class="real-road-list"><span>北 · 国帆路</span><span>东 · 江湾城路</span><span>南 · 殷高东路</span><span>西 · 国权北路</span></div>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">02</span><h2>候选起点</h2></div>
          <p v-if="!realCandidate" class="section-explain">返回地图，在围合区域内点击一个位置，或选用区域中心。</p>
          <div v-else class="real-selected-point"><span class="origin-pin"></span><div><strong>已记录候选位置</strong><small>{{ realCandidate.coordType === 'bd09ll' ? 'BD-09 坐标' : 'WGS-84 示意坐标 · 百度底图待配置' }}</small><code>{{ realCandidate.lng.toFixed(6) }}, {{ realCandidate.lat.toFixed(6) }}</code></div></div>
          <p class="origin-action-hint">选点到最近道路的虚线仅为未核实的演示接入，不代表可实际步行穿行。</p>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">03</span><h2>路线示意状态</h2></div>
          <p v-if="realAnalysisResult" class="section-explain">固定圆半径约 1,170 米；临时路网绘制 {{ realAnalysisResult.summary.routeSegmentCount }} 条可连通线段。圆不是 15 分钟等时圈，路线不用于设施覆盖判定。</p>
          <p v-else class="section-explain">在地图上选点并点击“生成示意分析”，即可预览固定圆和路线动画。未来真实结果会沿用同一显示接口。</p>
          <p class="section-explain">真实路网仍需覆盖选区外可达的外围，不能沿四路边界截断。</p>
          <button type="button" class="return-map-button real-demo-switch" @click="switchMapMode('synthetic'); goToPage(0)">体验专家模式 <span aria-hidden="true">↗</span></button>
        </section>
        <div class="panel-footer">边界与 SVG 示意数据 © OpenStreetMap contributors（ODbL）；百度底图启用后保留其原生版权标识。</div>
      </aside>

      <aside v-else-if="mapMode === 'synthetic'" class="real-insight-panel" aria-label="专家模式 C++ 合成路网分析信息">
        <div class="demo-warning real-data-warning">
          <span class="warning-icon">!</span>
          <span><strong>C++ 算法结果，合成路网数据</strong> · 绿色面来自引擎返回的 MultiPolygon，不是固定半径圆；道路两侧、过街与连接性尚未经现场核实，不能作为真实出行结论。</span>
        </div>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">01</span><h2>分析起点</h2></div>
          <p v-if="!cppCandidate" class="section-explain">返回地图，在四路围合区域内选择起点。选区外的路网仍参与 15 分钟计算。</p>
          <div v-else class="real-selected-point"><span class="origin-pin"></span><div><strong>已选择合成路网起点</strong><small>同一份 SVG 底图 · 选点仅限四路围合区</small><code>{{ cppCandidate.lng.toFixed(6) }}, {{ cppCandidate.lat.toFixed(6) }}</code></div></div>
          <p class="origin-action-hint">路外起点会先扣除到最近合成路段的估算步行时间；若 15 分钟内无法到达路段，引擎会拒绝分析。</p>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">02</span><h2>15 分钟路网等时圈</h2></div>
          <p v-if="cppAnalysisState === 'error'" class="section-explain" role="alert">{{ cppAnalysisError }}</p>
          <p v-else-if="cppAnalysisState === 'running'" class="section-explain" role="status">Python 正在把起点交给 C++ 引擎，结果返回后将绘制等时圈和可达街段。</p>
          <template v-else-if="cppAnalysisResult">
            <div class="summary-band">
              <div class="summary-primary"><strong>15<span>分钟</span></strong><small>路网步行阈值</small></div>
              <div class="summary-secondary"><strong class="summary-number">{{ cppAnalysisResult.summary.routeSegmentCount }}</strong><small>可达线段</small></div>
            </div>
            <p class="section-explain">距最近路段约 {{ Number(cppAnalysisResult.summary.originSnapMeters ?? 0).toFixed(1) }} 米，估算接入耗时 {{ Math.round(cppAnalysisResult.summary.originAccessSeconds ?? 0) }} 秒；剩余时间沿路网计算。虚线接入未核实，等时圈按 C++ 返回的多边形绘制。</p>
          </template>
          <p v-else class="section-explain">点击右上角“计算 15 分钟等时圈”后，展示 C++ Dijkstra 计算的可达街段及近似 MultiPolygon 面。</p>
        </section>
        <section v-if="cppAnalysisResult" class="panel-section">
          <PoiInventoryPanel :result="cppAnalysisResult" :category="cppPoiCategory"
            @category="cppPoiCategory = $event" @refresh="runCppAnalysis(true)" />
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">03</span><h2>数据与精度说明</h2></div>
          <p class="section-explain">路外选点会按到最近路段的直线距离扣除步行时间；这只是合成演示接入，不保证穿越建筑或地块可行。主干道用双侧人行道建模，其余道路暂按共享通道处理；部分过街连接为合成推断。图形不是经核实的真实 15 分钟等时圈。</p>
          <p class="section-explain">四路围合线仅限制起点，不裁切外围可达路段。百度 POI 点位采用缓存的坐标校准与路网近似对齐；圈内候选数量与模型步行可达分开统计，入口与清单未核齐时不输出真实灰区。</p>
          <button type="button" class="return-map-button real-demo-switch" @click="goToPage(0)">返回地图重新选点 <span aria-hidden="true">↗</span></button>
        </section>
        <div class="panel-footer">SVG 底图与边界数据 © OpenStreetMap contributors（ODbL）；合成路网仅用于算法联调。</div>
      </aside>
        </div>
      </div>
      <div
        ref="wordmarkEl"
        class="geoview-footer"
        aria-hidden="true"
        @pointerenter="moveWordmark"
        @pointermove="moveWordmark"
      >
        <svg ref="wordmarkSvg" class="geoview-art" viewBox="0 0 1000 180" preserveAspectRatio="xMidYMax meet" focusable="false">
          <defs>
            <clipPath id="geoview-letter-clip" clipPathUnits="userSpaceOnUse">
              <text class="geoview-clip-text" x="0" y="160">GEOVIEW</text>
            </clipPath>
            <radialGradient id="geoview-comet-gradient">
              <stop offset="0" stop-color="#d9fff1" stop-opacity="1" />
              <stop offset=".24" stop-color="#6ecdae" stop-opacity=".88" />
              <stop offset="1" stop-color="#55b795" stop-opacity="0" />
            </radialGradient>
          </defs>
          <g ref="cometLayer" class="geoview-comet" clip-path="url(#geoview-letter-clip)"></g>
          <text ref="wordmarkText" class="geoview-outline-text" x="0" y="160">GEOVIEW</text>
          <text class="geoview-hit" x="0" y="160">GEOVIEW</text>
        </svg>
      </div>
    </section>

    <nav class="page-pagination" aria-label="页面导航">
      <button type="button" :aria-current="activePage === 0 ? 'page' : undefined" aria-label="地图演示页" @click="goToPage(0)"><span></span></button>
      <button type="button" :aria-current="activePage === 1 ? 'page' : undefined" aria-label="生活圈控制面板" @click="goToPage(1)"><span></span></button>
    </nav>
  </div>
</template>
