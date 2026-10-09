<script setup>
import { onUnmounted, ref, watch } from "vue";
import RealMapStage from "./RealMapStage.vue";
import PoiInventoryPanel from "./PoiInventoryPanel.vue";
import NeighborhoodFooter from "./NeighborhoodFooter.vue";
import { visiblePois } from "./poiFacilities.js";
import { requestCppMapAnalysis, requestCppPoiRoute } from "./cppAnalysisClient.js";
import { requestSampledMapAnalysis, requestSampledPoiRoute } from "./sampledAnalysisClient.js";
import { getActiveRegion } from "./regionLoader.js";

const region = getActiveRegion();
const emit = defineEmits(['choose-region']);
const coverageSize = `${((region.boundsMeters.maxX - region.boundsMeters.minX) / 1000).toFixed(2)} × ${((region.boundsMeters.maxY - region.boundsMeters.minY) / 1000).toFixed(2)} 公里`;
const pageViewport = ref(null);
const detailPage = ref(null);
const activePage = ref(0);
const requestedMode = new URLSearchParams(window.location.search).get("mode");
const mapMode = ref(requestedMode === "synthetic" ? "synthetic" : "real");
const showBlindZones = ref(false);
const sharedMapMounted = ref(true);
const mapModes = [
  { id: "real", label: "真实区域", description: "百度采样与 Python 插值等时圈"  },
  { id: "synthetic", label: "专家模式", description: "C++ 路网等时圈，当前仍使用合成数据" },
];
const mapZoomTier = ref("medium");
const mapOverviewRequestId = ref(0);
const livingFooterCollapsed = ref(false);
const realCandidate = ref(null);
const realAnalysisResult = ref(null);
const realAnalysisState = ref("idle");
const realAnalysisError = ref("");
const realPoiRoute = ref(null);
let realPoiRouteAbort;
let realPoiRouteSequence = 0;
let realAnalysisAbort;
const cppCandidate = ref(null);
const cppAnalysisResult = ref(null);
const cppAnalysisState = ref("idle");
const cppAnalysisError = ref("");
const cppPoiCategory = ref("all");
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
    realAnalysisError.value = "示意路线暂时无法生成，请重新选点后重试。";
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
    realPoiRoute.value = { poiId: target.id, status: 'error', message: '请重新生成真实区域分析后再查询路线。' };
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
        : String(error?.message).includes('ROUTE_CONTEXT_EXPIRED') ? '该分析已过期，请重新生成真实区域分析。'
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
      ? "到最近合成路段已耗尽步行预算，请在道路附近重新选点。"
      : "C++ 分析未完成，请确认 Python 后端和 C++ 引擎已启动。";
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
  if (mode !== 'real') clearRealPoiRoute();
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
        <button type="button" class="header-nav-button" @click="goToPage(1)">查看报告 <span aria-hidden="true">↓</span></button>
        <a class="header-contact" href="https://github.com/Sa4pphire" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 联系项目作者">联系 <span class="github-label">/ GitHub</span> <span aria-hidden="true">↗</span></a>
      </div>
    </header>

    <main class="dashboard">
      <section class="map-column" aria-label="生活圈地图">
        <div class="map-frame">
          <div class="map-topline">
            <button v-if="mapMode === 'real'" type="button" class="analyze-button map-analyze-button real-mode-action" :class="{ 'is-running': realAnalysisState === 'running', 'is-complete': realAnalysisState === 'complete' }" :disabled="!realCandidate || realAnalysisState === 'running'" @click="runRealAnalysis">
              <span class="button-label">{{ !realCandidate ? '先在地图选点' : realAnalysisState === 'running' ? '正在绘制路线…' : realAnalysisState === 'complete' ? '重新生成示意' : '生成真实区域分析' }}</span><span class="button-arrow" aria-hidden="true">{{ realAnalysisState === 'complete' ? '✓' : realAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
            <button v-else-if="mapMode === 'synthetic'" type="button" class="analyze-button map-analyze-button real-mode-action"
              :class="{ 'is-running': cppAnalysisState === 'running', 'is-complete': cppAnalysisState === 'complete' }"
              :disabled="!cppCandidate || cppAnalysisState === 'running'" @click="runCppAnalysis">
              <span class="button-label">{{ !cppCandidate ? '先在地图选点' : cppAnalysisState === 'running' ? 'C++ 计算中…' : cppAnalysisResult ? '重新计算等时圈' : '计算 15 分钟等时圈' }}</span>
              <span class="button-arrow" aria-hidden="true">{{ cppAnalysisState === 'complete' ? '✓' : cppAnalysisState === 'running' ? '◌' : '↗' }}</span>
            </button>
          </div>

          <div class="map-canvas">
            <Transition name="map-mode">
              <RealMapStage v-if="sharedMapMounted"
                :analysis-mode="mapMode === 'synthetic' ? 'cpp' : 'preview'"
                :candidate="mapMode === 'synthetic' ? cppCandidate : realCandidate"
                :analysis-result="mapMode === 'synthetic' ? cppAnalysisResult : realAnalysisResult"
                :zoom-tier="mapZoomTier" :overview-request-id="mapOverviewRequestId"
                :show-blind-zones="showBlindZones"
                :poi-focus-request="mapMode === 'synthetic' ? cppPoiFocusRequest : null"
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
            <span v-if="mapMode === 'real'"><span class="line-signal"></span>{{ realAnalysisState === 'error' ? realAnalysisError : realAnalysisState === 'running' ? '正在生成百度采样等时圈，请稍候' : realAnalysisResult ? '百度采样等时圈与代表路线已显示' : realCandidate ? '已选起点 · 点击右上角生成真实区域分析' : '现有路网范围 · 点击地图选点' }}</span>
            <span v-else-if="mapMode === 'synthetic'" role="status"><span class="line-signal"></span>{{ cppAnalysisState === 'error' ? cppAnalysisError : cppAnalysisState === 'running' ? '正在计算路网等时圈' : cppAnalysisState === 'enriching' ? '等时圈已显示 · 正在补充设施，可继续选点' : cppAnalysisResult ? '等时圈已显示 · 点击设施查看路径（未核实）' : cppCandidate ? '已选起点 · 点击右上角计算等时圈' : '专家模式 · 合成路网，点击地图选点' }}</span>
            <span><span class="line-signal"></span>{{ showBlindZones ? (realAnalysisResult?.blindZoneStatus === 'confirmed' ? '等时圈内盲区候选 · 五类设施 · 直线服务半径 1 公里' : realAnalysisResult?.blindZoneStatus === 'provisional' ? '部分设施查询不完整：仅评估查询完成的类别' : realAnalysisResult?.blindZoneStatus === 'unknown' ? '设施检索不完整或不可用，暂无法判断盲区' : '计算真实区域分析后可查看服务覆盖候选') : '点击眼睛图标切换服务覆盖候选层' }}</span>
            <span><a v-if="region.source?.provider === 'OpenStreetMap contributors'" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">区域轮廓与路网 © OpenStreetMap contributors · ODbL</a><template v-else>区域轮廓与路网：{{ region.source?.provider || '当前区域包' }}</template></span>
          </div>
          <div v-if="mapMode === 'real' && realAnalysisResult" class="real-route-legend" aria-label="真实区域路线图例">
            <span><i class="legend-sampled-route"></i>采样边界路线 {{ realAnalysisResult.summary.samplingRouteCount }}</span>
            <span v-if="realPoiRoute?.status === 'ready'"><i class="legend-poi-route"></i>选中设施的百度步行路线</span>
            <span v-else><i class="legend-poi-route"></i>POI 服务路线 {{ realAnalysisResult.summary.poiRouteCount }}<small v-if="realAnalysisResult.summary.poiRouteCount === 0">（暂无 15 分钟内有效路线）</small></span>
            <span v-if="showBlindZones && ['confirmed', 'provisional'].includes(realAnalysisResult?.blindZoneStatus)"><i class="legend-blind-zone"></i>{{ realAnalysisResult.blindZoneStatus === 'provisional' ? '清单不完整 · 候选盲区' : '连续服务覆盖盲区候选' }}</span>
          </div>
        </div>
      </section>
    </main>
    <footer class="living-footer" :class="{ collapsed: livingFooterCollapsed }" aria-label="LIVING CIRCLE">
      <div class="living-meta" aria-hidden="true">
        <span>{{ region.id.toUpperCase() }} / {{ mapMode === 'real' ? 'AREA PREVIEW' : 'C++ ISOCHRONE' }}</span>
        <span>向下滚动 · 查看生活圈报告 ↓</span>
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

    <section ref="detailPage" class="book-page detail-page" aria-label="生活圈控制面板">
      <div class="detail-page-inner">
        <div class="detail-page-heading">
          <div>
            <p class="section-kicker">{{ mapMode === 'real' ? region.name : '专家模式 · C++ 算法演示' }}</p>
            <h2>{{ mapMode === 'real' ? '真实范围与选点状态' : '从地图走进路网计算' }}</h2>
          </div>
          <button type="button" class="return-map-button" @click="goToPage(0)">返回地图 <span aria-hidden="true">↑</span></button>
        </div>
      <div class="detail-scroll">

      <aside v-if="mapMode === 'real'" class="real-insight-panel" aria-label="真实区域预览信息">
        <div class="demo-warning real-data-warning">
          <span class="warning-icon">!</span>
          <span><strong>真实区域上的百度采样等时圈分析</strong> · 点击分析后展示固定半径圆与临时路线。道路类型、过街和设施入口均未经核查。</span>
        </div>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">01</span><h2>演示区域</h2></div>
          <p class="section-explain">地图拖动与选点范围由当前路网路径自动派生，覆盖约 {{ coverageSize }}。</p>
          <div class="real-road-list"><span>{{ region.nodeCount.toLocaleString() }} 个路网节点</span><span>{{ region.edgeCount.toLocaleString() }} 条路网边</span><span>区域版本 {{ region.version }}</span><span>数据范围自动计算</span></div>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">02</span><h2>候选起点</h2></div>
          <p v-if="!realCandidate" class="section-explain">返回地图，在现有路网数据范围内选择起点。</p>
          <div v-else class="real-selected-point"><span class="origin-pin"></span><div><strong>已记录候选位置</strong><small>{{ realCandidate.coordType === 'bd09ll' ? 'BD-09 坐标' : 'WGS-84 示意坐标 · 百度底图待配置' }}</small><code>{{ realCandidate.lng.toFixed(6) }}, {{ realCandidate.lat.toFixed(6) }}</code></div></div>
          <p class="origin-action-hint">选点到最近道路的虚线仅为未核实的演示接入，不代表可实际步行穿行。</p>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">03</span><h2>路线示意状态</h2></div>
          <p v-if="realAnalysisResult" class="section-explain">百度采样结果生成不规则 15 分钟等时圈，并绘制 {{ realAnalysisResult.summary.samplingRouteCount }} 条采样边界步行路线；另有 {{ realAnalysisResult.summary.poiRouteCount }} 个 POI 服务点路线<span v-if="realAnalysisResult.summary.poiRouteCount === 0">（当前检索到的服务点没有通过 15 分钟步行阈值）</span>。绿色线贴合采样边界，蓝色线指向服务点。</p>
          <p v-if="realAnalysisResult" class="section-explain">设施沿用学校、医院、商超、公共服务、餐饮五类规则。盲区仅在等时圈内显示，按 1 公里直线服务半径估算；圈外设施可覆盖圈内区域。查询不完整的类别不判定缺失，入口和清单未经核实。</p>
          <p v-if="realAnalysisResult" class="section-explain">点击地图上的设施可查询起点到该设施的百度步行参考路线，并显示预计耗时与距离。</p>
          <p v-else class="section-explain">在地图上选点并点击“生成真实区域分析”，即可请求百度采样、Python 插值和代表性步行路线。</p>
          <p class="section-explain">{{ region.coverageNotice }}</p>
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
          <p v-if="!cppCandidate" class="section-explain">返回地图，在当前步行图的数据范围内选择起点。</p>
          <div v-else class="real-selected-point"><span class="origin-pin"></span><div><strong>已选择合成路网起点</strong><small>当前区域包 · 选点范围由路网派生</small><code>{{ cppCandidate.lng.toFixed(6) }}, {{ cppCandidate.lat.toFixed(6) }}</code></div></div>
          <p class="origin-action-hint">路外起点会先扣除到最近合成路段的估算步行时间；若 15 分钟内无法到达路段，引擎会拒绝分析。</p>
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">02</span><h2>15 分钟路网等时圈</h2></div>
          <p v-if="cppAnalysisState === 'error'" class="section-explain" role="alert">{{ cppAnalysisError }}</p>
          <p v-else-if="cppAnalysisState === 'running'" class="section-explain" role="status">正在计算等时圈，完成后先显示范围，再补充设施点。</p>
          <template v-else-if="cppAnalysisResult">
            <div class="summary-band">
              <div class="summary-primary"><strong>15<span>分钟</span></strong><small>路网步行阈值</small></div>
              <div class="summary-secondary"><strong class="summary-number">{{ cppAnalysisResult.summary.routeSegmentCount }}</strong><small>可达线段</small></div>
            </div>
            <p class="section-explain">距最近路段约 {{ Number(cppAnalysisResult.summary.originSnapMeters ?? 0).toFixed(1) }} 米，估算接入耗时 {{ Math.round(cppAnalysisResult.summary.originAccessSeconds ?? 0) }} 秒；剩余时间沿路网计算。虚线接入未核实，等时圈按 C++ 返回的多边形绘制。</p>
            <p v-if="cppAnalysisResult.timingsMs.firstResult != null" class="section-explain" role="status">等时圈生成 {{ (cppAnalysisResult.timingsMs.firstResult / 1000).toFixed(2) }} 秒<span v-if="cppAnalysisResult.engineBuildMode !== 'unknown'">（{{ cppAnalysisResult.engineBuildMode }}）</span>；{{ cppAnalysisState === 'enriching' ? '设施正在补充，不影响地图操作。' : '设施更新不改变地图比例尺。' }}</p>
            <details class="section-explain" v-if="cppAnalysisResult.timingsMs.cpp != null">
              <summary>查看计算阶段耗时</summary>
              <div v-for="(milliseconds, stage) in cppAnalysisResult.timingsMs" :key="stage">{{ stage }}：{{ Number(milliseconds).toFixed(1) }} 毫秒</div>
            </details>
          </template>
          <p v-else class="section-explain">计算后展示等时圈面与设施点；点击设施后，单独绘制起点到绑定入口的 Dijkstra 最短路径。</p>
        </section>
        <section v-if="cppAnalysisResult" class="panel-section">
          <PoiInventoryPanel :result="cppAnalysisResult" :category="cppPoiCategory" :busy="cppAnalysisState === 'enriching'"
            @category="cppPoiCategory = $event" @refresh="runCppAnalysis(true)" @select="showCppPoiOnMap" />
        </section>
        <section class="panel-section">
          <div class="section-head"><span class="section-index">03</span><h2>数据与精度说明</h2></div>
          <p class="section-explain">路外选点会按到最近路段的直线距离扣除步行时间；这只是合成演示接入，不保证穿越建筑或地块可行。主干道用双侧人行道建模，其余道路暂按共享通道处理；部分过街连接为合成推断。图形不是经核实的真实 15 分钟等时圈。</p>
          <p class="section-explain">{{ region.coverageNotice }} 百度 POI 点位采用本区域包的坐标校准；圈内候选数量与模型步行可达分开统计，入口与清单未核齐时不输出真实灰区。</p>
          <button type="button" class="return-map-button real-demo-switch" @click="goToPage(0)">返回地图重新选点 <span aria-hidden="true">↗</span></button>
        </section>
        <div class="panel-footer">SVG 底图与边界数据 © OpenStreetMap contributors（ODbL）；合成路网仅用于算法联调。</div>
      </aside>
        </div>
      </div>
      <NeighborhoodFooter />
    </section>

    <nav class="page-pagination" aria-label="页面导航">
      <button type="button" :aria-current="activePage === 0 ? 'page' : undefined" aria-label="地图演示页" @click="goToPage(0)"><span></span></button>
      <button type="button" :aria-current="activePage === 1 ? 'page' : undefined" aria-label="生活圈控制面板" @click="goToPage(1)"><span></span></button>
    </nav>
  </div>
</template>
