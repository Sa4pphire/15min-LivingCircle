<script setup>
import { onUnmounted, ref, watch } from "vue";
import RealMapStage from "./RealMapStage.vue";
import LifeCircleReport from "./LifeCircleReport.vue";
import NeighborhoodFooter from "./NeighborhoodFooter.vue";
import { visiblePois } from "./poiFacilities.js";
import { requestCppMapAnalysis, requestCppPoiRoute } from "./cppAnalysisClient.js";
import { requestSampledMapAnalysis, requestSampledPoiRoute } from "./sampledAnalysisClient.js";
import { getActiveRegion } from "./regionLoader.js";

const region = getActiveRegion();
const emit = defineEmits(['choose-region']);
const pageViewport = ref(null);
const detailPage = ref(null);
const activePage = ref(0);
const requestedMode = new URLSearchParams(window.location.search).get("mode");
const mapMode = ref(requestedMode === "synthetic" ? "synthetic" : "real");
const showBlindZones = ref(false);
const sharedMapMounted = ref(true);
const mapModes = [
  { id: "real", label: "在线计算", description: "用在线地图查询步行范围和路线" },
  { id: "synthetic", label: "合成模拟", description: "用现有区域数据体验步行范围" },
];
const mapZoomTier = ref("medium");
const mapOverviewRequestId = ref(0);
const livingFooterCollapsed = ref(false);
const realCandidate = ref(null);
const realAnalysisResult = ref(null);
const realAnalysisState = ref("idle");
const realAnalysisError = ref("");
const realPoiRoute = ref(null);
const realPoiFocusRequest = ref(null);
let realPoiFocusSequence = 0;
let realPoiRouteAbort;
let realPoiRouteSequence = 0;
let realAnalysisAbort;
const cppCandidate = ref(null);
const cppAnalysisResult = ref(null);
const cppAnalysisState = ref("idle");
const cppAnalysisError = ref("");
const cppPoiFocusRequest = ref(null);
const cppPoiRoute = ref(null);
let cppPoiRouteAbort;
let cppPoiRouteSequence = 0;
let cppPoiFocusSequence = 0;
let cppAnalysisAbort;
const livingLetters = Array.from("LIVING CIRCLE");
let pageWheelDistance = 0;
let pageTurnTimer;
let pageTurning = false;

function chooseRealPoint(point) {
  clearRealPoiRoute();
  realPoiFocusRequest.value = null;
  realAnalysisAbort?.abort();
  realCandidate.value = point;
  realAnalysisResult.value = null;
  realAnalysisState.value = "idle";
  realAnalysisError.value = "";
}

async function runRealAnalysis() {
  if (!realCandidate.value?.local || realAnalysisState.value === "running") return;
  clearRealPoiRoute();
  realAnalysisAbort?.abort();
  const controller = new AbortController();
  realAnalysisAbort = controller;
  realAnalysisResult.value = null;
  realAnalysisError.value = "";
  realAnalysisState.value = "running";
  try {
    const result = await requestSampledMapAnalysis(
      realCandidate.value,
      { signal: controller.signal, regionId: region.id },
    );
    if (controller.signal.aborted) return;
    realAnalysisResult.value = result;
    realAnalysisState.value = "complete";
  } catch (error) {
    if (controller.signal.aborted) return;
    realAnalysisState.value = "error";
    realAnalysisError.value = "这次计算没有完成，请重新选点后再试。";
    console.warn("真实区域合成分析失败。", error);
  }
}

function clearRealPoiRoute() {
  realPoiRouteAbort?.abort();
  realPoiRouteAbort = null;
  realPoiRouteSequence += 1;
  realPoiRoute.value = null;
}

async function selectRealPoi(poi) {
  const target = visiblePois(realAnalysisResult.value).find(entry => entry.id === poi?.id);
  if (!target || mapMode.value !== 'real') return;
  if (realPoiRoute.value?.poiId === target.id && ['loading', 'ready'].includes(realPoiRoute.value.status)) return;
  clearRealPoiRoute();
  const analysisId = realAnalysisResult.value.analysisId;
  if (!analysisId) {
    realPoiRoute.value = { poiId: target.id, status: 'error', message: '请先重新计算步行范围，再查看路线。' };
    return;
  }
  const sequence = realPoiRouteSequence;
  const controller = new AbortController();
  realPoiRouteAbort = controller;
  realPoiRoute.value = { poiId: target.id, status: 'loading', algorithm: 'baidu_walking' };
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
  try {
    const route = await requestSampledPoiRoute({ analysisId, poiId: target.id }, { signal: controller.signal });
    if (sequence !== realPoiRouteSequence || controller.signal.aborted) return;
    realPoiRoute.value = { ...route, sequence };
  } catch (error) {
    if (sequence !== realPoiRouteSequence || (controller.signal.aborted && !timedOut)) return;
    realPoiRoute.value = { poiId: target.id, status: 'error', algorithm: 'baidu_walking',
      message: timedOut ? '百度路线查询超时，请稍后重试。'
        : String(error?.message).includes('ROUTE_CONTEXT_EXPIRED') ? '这个结果已过期，请重新计算步行范围。'
        : '百度步行路线暂时无法获取，请稍后重试。' };
  } finally {
    clearTimeout(timeout);
    if (sequence === realPoiRouteSequence) realPoiRouteAbort = null;
  }
}

function selectMapPoi(poi) {
  if (mapMode.value === 'real') void selectRealPoi(poi);
  else void selectCppPoi(poi);
}

function dismissMapPoi() {
  if (mapMode.value === 'real') clearRealPoiRoute();
  else clearCppPoiRoute();
}

function chooseCppPoint(point) {
  if (cppAnalysisState.value === "running") return;
  clearCppPoiRoute();
  cppPoiFocusRequest.value = null;
  cppAnalysisAbort?.abort();
  cppCandidate.value = point;
  cppAnalysisResult.value = null;
  if (mapZoomTier.value === "result") mapZoomTier.value = "medium";
  cppAnalysisState.value = "idle";
  cppAnalysisError.value = "";
}

async function runCppAnalysis(refreshPois = false) {
  if (!cppCandidate.value?.local || cppAnalysisState.value === "running") return;
  clearCppPoiRoute();
  cppPoiFocusRequest.value = null;
  cppAnalysisAbort?.abort();
  const controller = new AbortController();
  cppAnalysisAbort = controller;
  cppAnalysisResult.value = null;
  cppAnalysisError.value = "";
  cppAnalysisState.value = "running";
  try {
    const result = await requestCppMapAnalysis({ origin: cppCandidate.value.local,
      includePois: true, refreshPois: refreshPois === true },
      { signal: controller.signal,
        onResult: (partial, { complete }) => {
          if (controller.signal.aborted) return;
          cppAnalysisResult.value = partial;
          cppAnalysisState.value = complete ? "complete" : "enriching";
        } });
    if (controller.signal.aborted) return;
    cppAnalysisResult.value = result;
    cppAnalysisState.value = "complete";
  } catch (error) {
    if (controller.signal.aborted) return;
    cppAnalysisState.value = cppAnalysisResult.value ? "complete" : "error";
    cppAnalysisError.value = String(error?.message).includes("ORIGIN_NOT_ON_WALKWAY")
      ? "这个起点离可走的道路太远，请在道路附近重新选点。"
      : "这次计算没有完成，请稍后重试。";
    console.warn("合成路网 C++ 分析失败。", error);
  }
}

function showCppPoiOnMap(poi) {
  // Resolve by ID from the current report, not a stale list item or its name.
  const target = visiblePois(cppAnalysisResult.value).find(entry => entry.id === poi?.id);
  if (!target) return;
  switchMapMode("synthetic");
  cppPoiFocusRequest.value = { id: target.id, sequence: ++cppPoiFocusSequence };
  goToPage(0);
  void selectCppPoi(target);
}

function showReportPoiOnMap(poi) {
  if (mapMode.value === 'synthetic') { showCppPoiOnMap(poi); return; }
  const target = visiblePois(realAnalysisResult.value).find(entry => entry.id === poi?.id);
  if (!target) return;
  realPoiFocusRequest.value = { id: target.id, sequence: ++realPoiFocusSequence };
  goToPage(0);
  void selectRealPoi(target);
}

function showReportServices() {
  switchMapMode('real');
  showBlindZones.value = true;
  goToPage(0);
}

function clearCppPoiRoute() {
  cppPoiRouteAbort?.abort();
  cppPoiRouteAbort = null;
  cppPoiRouteSequence += 1;
  cppPoiRoute.value = null;
}

async function selectCppPoi(poi) {
  const target = visiblePois(cppAnalysisResult.value).find(entry => entry.id === poi?.id);
  if (!target || mapMode.value !== "synthetic") return;
  clearCppPoiRoute();
  const analysisId = cppAnalysisResult.value.analysisId;
  if (!analysisId) {
    cppPoiRoute.value = { poiId: target.id, status: "error", message: "请重新计算等时圈后再规划设施路径。" };
    return;
  }
  const sequence = cppPoiRouteSequence;
  const controller = new AbortController();
  cppPoiRouteAbort = controller;
  cppPoiRoute.value = { poiId: target.id, status: "loading" };
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
  try {
    const route = await requestCppPoiRoute({ analysisId, poiId: target.id }, { signal: controller.signal });
    if (sequence !== cppPoiRouteSequence || controller.signal.aborted) return;
    cppPoiRoute.value = { ...route, sequence };
  } catch (error) {
    if (sequence !== cppPoiRouteSequence || (controller.signal.aborted && !timedOut)) return;
    cppPoiRoute.value = { poiId: target.id, status: "error",
      message: timedOut ? "路径计算超时，请稍后重试。" : String(error?.message).includes("ROUTE_CONTEXT_EXPIRED")
        ? "该分析已过期，请重新计算等时圈。" : "路径暂时无法生成，请确认后端与最新 C++ 引擎已启动。" };
  } finally {
    clearTimeout(timeout);
    if (sequence === cppPoiRouteSequence) cppPoiRouteAbort = null;
  }
}

watch(mapMode, mode => {
  if (mode !== 'synthetic') clearCppPoiRoute();
  if (mode !== 'real') { clearRealPoiRoute(); realPoiFocusRequest.value = null; }
});

function switchMapMode(mode) {
  if (!["real", "synthetic"].includes(mode)) return;
  showBlindZones.value = false;
  if (mapMode.value === mode) return;
  if (mapZoomTier.value === "result") mapZoomTier.value = "medium";
  mapMode.value = mode;
}

function toggleBlindZones() {
  // Keep the existing real-area layer behavior without treating it as a mode.
  if (mapMode.value !== "real") mapMode.value = "real";
  showBlindZones.value = !showBlindZones.value;
}

function toggleLivingFooter() {
  livingFooterCollapsed.value = !livingFooterCollapsed.value;
}

function goToPage(index) {
  const targetPage = Math.min(1, Math.max(0, index));
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
  if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  // The map owns wheel gestures. Let Baidu zoom the live map instead of the
  // page-level snap scroller stealing the gesture.
  if (event.target instanceof Element && event.target.closest(".real-map-stage")) return;
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

onUnmounted(() => {
  clearRealPoiRoute();
  clearCppPoiRoute();
  clearCppPoiRoute();
  realAnalysisAbort?.abort();
  cppAnalysisAbort?.abort();
  clearTimeout(pageTurnTimer);
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
        <span>{{ region.name }}</span>
      </div>
      <div class="header-right">
        <button type="button" class="header-nav-button region-switch-button" @click="emit('choose-region')">切换区域</button>
        <a class="header-nav-button" :href="`/?mode=editor&region=${encodeURIComponent(region.id)}`">编辑路网</a>
        <button type="button" class="header-nav-button" @click="goToPage(1)">查看结果 <span aria-hidden="true">↓</span></button>
        <a class="header-contact" href="https://github.com/Sa4pphire" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 联系项目作者">联系 <span class="github-label">/ GitHub</span> <span aria-hidden="true">↗</span></a>
      </div>
    </header>

    <main class="dashboard">
      <section class="map-column" aria-label="生活圈地图">
        <div class="map-frame">
          <div class="map-topline">
            <button v-if="mapMode === 'real'" type="button" class="analyze-button map-analyze-button real-mode-action" :class="{ 'is-running': realAnalysisState === 'running', 'is-complete': realAnalysisState === 'complete' }" :disabled="!realCandidate || realAnalysisState === 'running'" @click="runRealAnalysis">
              <span class="button-label">{{ !realCandidate ? '先在地图选点' : realAnalysisState === 'running' ? '正在计算…' : realAnalysisState === 'complete' ? '重新计算范围' : '计算 15 分钟范围' }}</span><span class="button-arrow" aria-hidden="true">{{ realAnalysisState === 'complete' ? '✓' : realAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
            <button v-else-if="mapMode === 'synthetic'" type="button" class="analyze-button map-analyze-button real-mode-action"
              :class="{ 'is-running': cppAnalysisState === 'running', 'is-complete': cppAnalysisState === 'complete' }"
              :disabled="!cppCandidate || cppAnalysisState === 'running'" @click="runCppAnalysis">
              <span class="button-label">{{ !cppCandidate ? '先在地图选点' : cppAnalysisState === 'running' ? '正在计算…' : cppAnalysisResult ? '重新计算范围' : '计算 15 分钟范围' }}</span>
              <span class="button-arrow" aria-hidden="true">{{ cppAnalysisState === 'complete' ? '✓' : cppAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
          </div>

          <div class="map-canvas">
            <Transition name="map-mode">
              <RealMapStage v-if="sharedMapMounted"
                :analysis-mode="mapMode === 'synthetic' ? 'cpp' : 'preview'"
                :candidate="mapMode === 'synthetic' ? cppCandidate : realCandidate"
                :analysis-result="mapMode === 'synthetic' ? cppAnalysisResult : realAnalysisResult"
                :analysis-complete="mapMode === 'synthetic' ? cppAnalysisState === 'complete' : realAnalysisState === 'complete'"
                :zoom-tier="mapZoomTier" :overview-request-id="mapOverviewRequestId"
                :show-blind-zones="showBlindZones"
                :poi-focus-request="mapMode === 'synthetic' ? cppPoiFocusRequest : realPoiFocusRequest"
                :poi-route="mapMode === 'synthetic' ? cppPoiRoute : realPoiRoute"
                :selection-disabled="mapMode === 'synthetic' && cppAnalysisState === 'running'"
                @poi-select="selectMapPoi" @poi-dismiss="dismissMapPoi"
                @select="mapMode === 'synthetic' ? chooseCppPoint($event) : chooseRealPoint($event)" />
            </Transition>
            <!-- Replay a light reveal without remounting the shared base map. -->
            <div :key="mapMode" class="map-mode-wash" aria-hidden="true"></div>
            <div class="map-layer-controls"
              @pointerdown.stop @click.stop @dblclick.stop>
              <div class="map-mode-switch" role="group" aria-label="地图展示模式"
                :style="{ '--mode-index': mapModes.findIndex(mode => mode.id === mapMode), '--mode-count': mapModes.length }">
                <span class="map-mode-indicator" aria-hidden="true"></span>
                <button v-for="mode in mapModes" :key="mode.id" type="button" :title="mode.description"
                  :aria-pressed="mapMode === mode.id" :class="{ active: mapMode === mode.id }"
                  @click="switchMapMode(mode.id)">{{ mode.label }}</button>
              </div>
              <button type="button" class="map-round-control map-blind-toggle" role="switch" aria-label="盲区显示"
                :aria-checked="showBlindZones" :class="{ 'is-on': showBlindZones }"
                @click="toggleBlindZones">
                <svg class="map-control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z" />
                  <circle cx="12" cy="12" r="2.6" />
                </svg>
                <span class="map-control-tooltip map-blind-tooltip" aria-hidden="true">盲区显示</span>
              </button>
            </div>
            <button type="button" class="map-round-control living-footer-toggle" aria-label="LIVING CIRCLE 装饰"
              :aria-expanded="!livingFooterCollapsed" :aria-pressed="!livingFooterCollapsed"
              :class="{ 'is-on': !livingFooterCollapsed }" aria-controls="living-wordmark-panel"
              @pointerdown.stop @dblclick.stop @click.stop="toggleLivingFooter">
              <svg class="map-control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M5 7V5h14v2M12 5v14M8.5 19h7" />
              </svg>
              <span class="map-control-tooltip" aria-hidden="true">{{ livingFooterCollapsed ? '显示装饰' : '隐藏装饰' }}</span>
            </button>
            <div class="map-compass" aria-hidden="true"><span>北</span><i></i></div>
          </div>

          <div class="map-bottomline real-mode">
            <span v-if="mapMode === 'real'"><span class="line-signal"></span>{{ realAnalysisState === 'error' ? realAnalysisError : realAnalysisState === 'running' ? '正在计算步行范围，请稍候' : realAnalysisResult ? '步行范围和参考路线已显示' : realCandidate ? '已选起点 · 点击右上角计算步行范围' : '点击地图，选个出发点' }}</span>
            <span v-else-if="mapMode === 'synthetic'" role="status"><span class="line-signal"></span>{{ cppAnalysisState === 'error' ? cppAnalysisError : cppAnalysisState === 'running' ? '正在计算步行范围' : cppAnalysisState === 'enriching' ? '范围已显示 · 正在补充地点' : cppAnalysisResult ? '范围已显示 · 点击地点查看路线' : cppCandidate ? '已选起点 · 点击右上角计算步行范围' : '合成模拟 · 点击地图选个起点' }}</span>
            <span><span class="line-signal"></span>{{ showBlindZones ? (realAnalysisResult?.blindZoneStatus === 'confirmed' ? '附近服务提醒 · 看看哪些地方离生活服务较远' : realAnalysisResult?.blindZoneStatus === 'provisional' ? '部分地点信息还不齐，先看看已找到的服务' : realAnalysisResult?.blindZoneStatus === 'unknown' ? '地点信息还不齐，暂时无法显示服务提醒' : '计算范围后，可查看附近服务提醒') : '点击眼睛图标，查看附近服务提醒' }}</span>
            <span><a v-if="region.source?.provider === 'OpenStreetMap contributors'" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">区域轮廓与路网 © OpenStreetMap contributors · ODbL</a><template v-else>区域轮廓与路网：{{ region.source?.provider || '当前区域包' }}</template></span>
          </div>
          <div v-if="mapMode === 'real' && realAnalysisResult" class="real-route-legend" aria-label="在线计算路线图例">
            <span><i class="legend-sampled-route"></i>步行参考路线 {{ realAnalysisResult.summary.samplingRouteCount }}</span>
            <span v-if="realPoiRoute?.status === 'ready'"><i class="legend-poi-route"></i>选中设施的百度步行路线</span>
            <span v-else><i class="legend-poi-route"></i>地点参考路线 {{ realAnalysisResult.summary.poiRouteCount }}<small v-if="realAnalysisResult.summary.poiRouteCount === 0">（暂时没有 15 分钟内的参考路线）</small></span>
            <span v-if="showBlindZones && ['confirmed', 'provisional'].includes(realAnalysisResult?.blindZoneStatus)"><i class="legend-blind-zone"></i>{{ realAnalysisResult.blindZoneStatus === 'provisional' ? '附近服务提醒（信息待补充）' : '附近服务提醒' }}</span>
          </div>
        </div>
      </section>
    </main>
    <footer class="living-footer" :class="{ collapsed: livingFooterCollapsed }" aria-label="LIVING CIRCLE">
      <div class="living-meta" aria-hidden="true">
        <span>{{ region.name }} / {{ mapMode === 'real' ? '在线计算' : '合成模拟' }}</span>
        <span>向下滚动 · 查看生活圈结果 ↓</span>
      </div>
      <div id="living-wordmark-panel" class="living-wordmark" aria-hidden="true">
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
    </footer>
    </section>

    <section ref="detailPage" class="book-page detail-page public-report-page" aria-label="生活圈结果">
      <div class="detail-page-inner">
        <LifeCircleReport :region="region" :mode="mapMode" :active="activePage === 1"
          :result="mapMode === 'real' ? realAnalysisResult : cppAnalysisResult"
          :state="mapMode === 'real' ? realAnalysisState : cppAnalysisState"
          :origin="mapMode === 'real' ? realCandidate : cppCandidate"
          :route="mapMode === 'real' ? realPoiRoute : cppPoiRoute"
          @map="goToPage(0)" @mode="switchMapMode" @select="showReportPoiOnMap" @services="showReportServices" />
      </div>
      <NeighborhoodFooter />
    </section>

    <nav class="page-pagination" aria-label="页面导航">
      <button type="button" :aria-current="activePage === 0 ? 'page' : undefined" aria-label="地图演示页" @click="goToPage(0)"><span></span></button>
      <button type="button" :aria-current="activePage === 1 ? 'page' : undefined" aria-label="生活圈结果" @click="goToPage(1)"><span></span></button>
    </nav>
  </div>
</template>
