<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { requestLocalExperiment } from "./localExperimentClient.js";
import PoiInventoryPanel from "./PoiInventoryPanel.vue";
import { poiAccessLabel, poiCategoryStyles, visiblePois } from "./poiFacilities.js";
import {
  localCategoryLabels, localExperimentCategories, localExperimentPlot,
  selectLocalWalkwayPoint,
} from "./localExperimentGeometry.js";

const lng = ref("");
const lat = ref("");
const coordType = ref("wgs84ll");
const originEdgeId = ref("");
const result = ref(null);
const category = ref("");
const state = ref("idle");
const error = ref("");
const queriedCenter = ref(null);
const poiCategory = ref("all");
const selectedPoi = ref(null);
const svgEl = ref(null);
const viewSize = ref({ width: 1000, height: 360 });
let controller;
let observer;

const categories = computed(() => localExperimentCategories(result.value));
const plot = computed(() => localExperimentPlot(result.value, category.value,
  viewSize.value.width, viewSize.value.height));
const validPoint = computed(() => lng.value !== "" && lat.value !== "" &&
  Number.isFinite(Number(lng.value)) && Number.isFinite(Number(lat.value)) &&
  Math.abs(Number(lng.value)) <= 180 && Math.abs(Number(lat.value)) < 90);
const sourceLabel = computed(() => result.value?.metadata.networkSource === "synthetic"
  ? "合成测试数据，不代表真实街区" : "已加载局部标注数据，不可外推街道结论");
const inventoryVerified = computed(() =>
  result.value?.metadata.facilityInventoryStatusByCategory?.[category.value] === "verified");
const originPixel = computed(() => plot.value && queriedCenter.value
  ? plot.value.project([queriedCenter.value.lng, queriedCenter.value.lat]) : null);

function poiPixel(poi) {
  const origin = result.value?.metadata.poi?.networkOrigin;
  const point = poi.localPointMeters;
  if (!plot.value || !origin || !point) return null;
  // Plot in the graph's calibrated meter frame, not with native BD-09 numbers
  // passed into a WGS-84 projector. The original BD-09 point stays intact.
  const displayPoint = [origin.lng + point[0] / (111320 * Math.cos(origin.lat * Math.PI / 180)),
    origin.lat + point[1] / 111320];
  return plot.value.project(displayPoint);
}

function fillExample() {
  // Existing w:154811345:2:0 midpoint in synthetic-preview.json; no new road data.
  lng.value = "121.504429458";
  lat.value = "31.331174183";
  coordType.value = "wgs84ll";
  originEdgeId.value = "w:154811345:2:0";
}

async function run(refreshPois = false) {
  if (!validPoint.value || state.value === "running") return;
  controller?.abort();
  controller = new AbortController();
  const candidate = { lng: Number(lng.value), lat: Number(lat.value), coordType: coordType.value };
  state.value = "running";
  error.value = "";
  result.value = null;
  try {
    const received = await requestLocalExperiment({ candidate,
      originEdgeId: originEdgeId.value, refreshPois: refreshPois === true }, { signal: controller.signal });
    if (controller.signal.aborted) return;
    result.value = received;
    selectedPoi.value = null;
    category.value = localExperimentCategories(received)[0] ?? "";
    queriedCenter.value = candidate;
    state.value = "complete";
  } catch (cause) {
    if (controller.signal.aborted) return;
    state.value = "error";
    const message = String(cause?.message ?? cause);
    error.value = message.includes("UNSUPPORTED_AREA")
      ? `局部路网无法加载：${message.replace("UNSUPPORTED_AREA: ", "")}。请核对 LOCAL_EXPERIMENT_NETWORK_PATH 与所选坐标系。`
      : message.includes("AMBIGUOUS_ORIGIN_SIDE")
        ? "道路侧不明确，请在接入边 ID 中指定所在的人行道。"
        : message.includes("ORIGIN_NOT_ON_WALKWAY")
          ? "起点不在已标注的步行空间内，请选择已知步行边上的位置。"
          : `局部计算未完成，请检查 Python 后端和 C++ 引擎。${message}`;
  }
}

function chooseFromStreet(event, walkway) {
  if (state.value === "running" || !svgEl.value || !plot.value) return;
  const matrix = svgEl.value.getScreenCTM();
  if (!matrix) return;
  const cursor = svgEl.value.createSVGPoint();
  cursor.x = event.clientX;
  cursor.y = event.clientY;
  const pixel = cursor.matrixTransform(matrix.inverse());
  const selected = selectLocalWalkwayPoint([pixel.x, pixel.y], walkway, plot.value);
  if (!selected) return;
  lng.value = selected.lng.toFixed(9);
  lat.value = selected.lat.toFixed(9);
  originEdgeId.value = selected.originEdgeId;
  coordType.value = selected.coordType;
}

watch(svgEl, (element) => {
  observer?.disconnect();
  if (!element) return;
  observer = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect;
    if (width > 0 && height > 0) viewSize.value = { width, height };
  });
  observer.observe(element);
});

onUnmounted(() => { controller?.abort(); observer?.disconnect(); });
</script>

<template>
  <section class="local-experiment-stage" aria-label="3 分钟局部路网实验">
    <form class="local-origin-form" @submit.prevent="run">
      <label>坐标系<select v-model="coordType" :disabled="state === 'running'"><option value="wgs84ll">WGS-84（现有合成路网）</option><option value="bd09ll">BD-09（显式配置路网）</option></select></label>
      <label>经度<input v-model="lng" inputmode="decimal" placeholder="如 121.504429458" required :disabled="state === 'running'" /></label>
      <label>纬度<input v-model="lat" inputmode="decimal" placeholder="如 31.331174183" required :disabled="state === 'running'" /></label>
      <label>接入边 ID <small>道路侧不明确时必填</small><input v-model="originEdgeId" placeholder="originEdgeId" :disabled="state === 'running'" /></label>
      <button class="local-run" type="submit" :disabled="!validPoint || state === 'running'">{{ state === 'running' ? 'C++ 计算中…' : '计算 3 分钟街段' }}</button>
      <button class="local-example" type="button" :disabled="state === 'running'" @click="fillExample">填入现有路网起点</button>
    </form>
    <p class="local-coordinate-hint">默认读取 synthetic-preview.json，使用 WGS-84 起点；局部模式须从已有步行边接入，不估算穿越地块的直线。显式更换文件时，请使用该文件声明的坐标系。</p>
    <p v-if="error" class="local-error" role="alert">{{ error }}</p>
    <template v-if="result">
      <div class="local-result-bar">
        <label v-if="categories.length">设施类别 <select v-model="category"><option v-for="id in categories" :key="id" :value="id">{{ localCategoryLabels[id] ?? id }}</option></select></label>
        <span v-else>当前仅计算可达街段</span>
        <span>{{ sourceLabel }}</span>
      </div>
      <div v-if="categories.length" class="local-legend" aria-label="局部实验街段图例">
        <span><i class="covered"></i>已覆盖</span><span><i class="candidate_uncovered"></i>候选未覆盖</span><span><i class="unknown"></i>未知</span>
        <small>{{ inventoryVerified ? '该类局部设施清单已核查' : '该类设施清单未核齐，未覆盖部分仅标未知' }}</small>
      </div>
      <p v-else class="local-boundary-warning" role="status">设施灰区数据不足：现有文件未提供设施及评价类别，不生成覆盖或候选灰区，不补造设施。</p>
      <svg v-if="plot" ref="svgEl" class="local-network-view" :viewBox="`0 0 ${viewSize.width} ${viewSize.height}`" role="img" aria-label="局部可达街段；绿色已覆盖，橙色候选未覆盖，灰蓝虚线未知">
        <text :x="viewSize.width - 20" y="26" text-anchor="end" class="local-north">北 ↑</text>
        <path v-for="walkway in plot.walkways" :key="`base-${walkway.id}`" :d="walkway.path" class="local-walkway" :class="{ unclassified: !categories.length }" />
        <path v-for="segment in plot.segments" :key="segment.id" :d="segment.path" class="local-classified" :class="segment.classification"><title>{{ segment.edgeId }}</title></path>
        <path v-for="walkway in plot.selectableWalkways" :key="`select-${walkway.id}`" :d="walkway.path" class="local-street-hit" @click="chooseFromStreet($event, walkway)"><title>选取 {{ walkway.edgeId }} 上的起点</title></path>
        <g v-if="originPixel" :transform="`translate(${originPixel[0]}, ${originPixel[1]})`"><circle r="10" class="local-origin-halo" /><circle r="4" class="local-origin-dot" /><text x="14" y="-12" class="local-origin-label">本次起点</text></g>
        <g v-for="poi in visiblePois(result, poiCategory).filter(item => poiPixel(item))" :key="poi.id"
          :transform="`translate(${poiPixel(poi)[0]}, ${poiPixel(poi)[1]})`" class="local-poi" role="button" tabindex="0"
          :aria-label="poi.name" @click.stop="selectedPoi = poi" @keydown.enter="selectedPoi = poi" @keydown.space.prevent="selectedPoi = poi">
          <circle r="10" :stroke="poiCategoryStyles[poi.category].color" /><text text-anchor="middle" dominant-baseline="central" :fill="poiCategoryStyles[poi.category].color">{{ poiCategoryStyles[poi.category].glyph }}</text>
        </g>
      </svg>
      <p class="local-network-caption">数据文件：{{ result.metadata.networkFile }}。仅显示引擎返回的街段，不叠加底图或推断灰区面。点击人行道或共享通道可填入新起点，再点击计算。当前结果起点（{{ result.metadata.coordType }}）：{{ queriedCenter.lng.toFixed(6) }}, {{ queriedCenter.lat.toFixed(6) }}。</p>
      <p v-if="result.warnings?.includes('LOCAL_REACHABILITY_MAY_BE_TRUNCATED')" class="local-boundary-warning" role="status">局部可达范围可能截断：本次起点在 180 秒内可到达裁剪出口。</p>
      <p v-if="result.metadata.topologyStatus !== 'verified'" class="local-boundary-warning">路网连接或裁剪出口尚未完整核查，未覆盖部分只能解释为未知。</p>
      <p v-if="selectedPoi" class="local-network-caption">{{ selectedPoi.name }}：{{ poiAccessLabel(selectedPoi) }}</p>
      <PoiInventoryPanel :result="result" :category="poiCategory" @category="poiCategory = $event"
        @select="selectedPoi = $event" @refresh="run(true)" />
      <p class="local-scope-note">候选未覆盖只说明已核查的小路网内暂未找到阈值内服务，不代表新江湾城真实设施匮乏；靠近裁剪出口的未知街段可能受到区外设施影响。</p>
    </template>
    <div v-else class="local-empty" :aria-busy="state === 'running'" role="status">
      <strong>{{ state === 'running' ? '正在分析局部路网…' : '先输入局部路网内的起点' }}</strong>
      <p>180 秒步行预算 · 三种街段状态 · 不进入 15 分钟体检报告</p>
      <p>当前统一使用已经建模的 synthetic-preview.json，不加载额外的小路网样例。</p>
    </div>
  </section>
</template>

<style scoped>
.local-experiment-stage { height: 100%; overflow-y: auto; overscroll-behavior: contain; padding: 14px 22px 20px; color: #193a31; background: #eaf0ec; }
.local-origin-form { display: flex; align-items: end; flex-wrap: wrap; gap: 10px; }
.local-origin-form label { display: grid; gap: 5px; font-size: 11px; font-weight: 600; }
.local-origin-form label small { font-weight: 400; color: #5d6785; }
.local-origin-form input, .local-origin-form select, .local-result-bar select { min-width: 0; border: 1px solid #b5c8bc; border-radius: 4px; background: #fff; color: #193a31; padding: 8px 10px; font: inherit; font-size: 12px; }
.local-origin-form input { width: 176px; }
.local-origin-form input:focus-visible, .local-result-bar select:focus-visible { outline: 3px solid #b55325; outline-offset: 2px; }
.local-origin-form button { padding: 9px 13px; border-radius: 4px; border: 1px solid #227759; font-size: 12px; cursor: pointer; }
.local-run { background: #227759; color: #fff; }
.local-example { background: transparent; color: #227759; }
.local-coordinate-hint, .local-network-caption, .local-scope-note { margin: 10px 0 0; font-size: 11px; line-height: 1.6; max-width: 1000px; color: #536d61; }
.local-error, .local-boundary-warning { margin: 10px 0; padding: 9px 12px; border-left: 3px solid #b55325; background: #fff; color: #91451f; font-size: 12px; line-height: 1.6; }
.local-result-bar { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 12px; font-size: 12px; }
.local-result-bar label { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.local-result-bar > span { color: #91451f; }
.local-legend { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 20px; margin: 10px 0; font-size: 11px; }
.local-legend span { display: inline-flex; align-items: center; gap: 6px; }
.local-legend i { width: 24px; border-top: 4px solid; }
.local-legend i.covered { border-color: #227759; }
.local-legend i.candidate_uncovered { border-color: #b55325; }
.local-legend i.unknown { border-color: #5d6785; border-top-style: dashed; }
.local-legend small { color: #536d61; }
.local-network-view { display: block; width: 100%; height: clamp(170px, 24vh, 360px); min-height: 170px; background: #f7faf8; border: 1px solid #c7d3cd; }
.local-walkway, .local-classified, .local-street-hit { fill: none; stroke-linecap: round; stroke-linejoin: round; }
.local-walkway { stroke: #c7d3cd; stroke-width: 12; }
.local-walkway.unclassified { stroke: #536d61; stroke-width: 5; }
.local-classified { stroke-width: 6; }
.local-classified.covered { stroke: #227759; }
.local-classified.candidate_uncovered { stroke: #b55325; }
.local-classified.unknown { stroke: #5d6785; stroke-dasharray: 9 7; }
.local-street-hit { stroke: transparent; stroke-width: 22; cursor: crosshair; }
.local-origin-halo { fill: #fff; stroke: #193a31; stroke-width: 1.5; }
.local-origin-dot { fill: #193a31; }
.local-poi { cursor: pointer; }
.local-poi circle { fill: #fbfdfb; stroke-width: 2; }
.local-poi text { font-size: 10px; font-weight: 700; pointer-events: none; }
.local-poi:focus-visible { outline: 2px solid #2f6e91; outline-offset: 2px; }
.local-origin-label, .local-north { fill: #193a31; font-size: 13px; }
.local-empty { display: grid; align-content: center; min-height: 180px; height: 70%; text-align: center; font-size: 12px; }
.local-empty strong { font-size: 18px; font-weight: 600; }
.local-empty p { margin: 12px 0 0; color: #536d61; }
@media (max-width: 600px) {
  .local-experiment-stage { padding: 10px 12px 20px; }
  .local-origin-form label { flex: 1 1 140px; }
  .local-origin-form input { width: 100%; }
  .local-result-bar { align-items: start; flex-direction: column; gap: 8px; }
  .local-network-view { min-height: 170px; }
}
</style>
