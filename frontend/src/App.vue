<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  buildingPois,
  categories,
  facilities,
  gapStreets,
  interactiveStreets,
  mockWalkingMinutes,
  presets,
  streets,
} from "./demoData";

const mapSvg = ref(null);
const probeEl = ref(null);
const pageViewport = ref(null);
const detailPage = ref(null);
const wordmarkEl = ref(null);
const wordmarkSvg = ref(null);
const wordmarkText = ref(null);
const cometLayer = ref(null);
const activePage = ref(0);
const candidate = ref({ ...presets[0] });
const analysisOrigin = ref({ ...presets[0] });
const selectedCategory = ref("shopping");
const selectedFacilityId = ref(null);
const hoveredFacilityId = ref(null);
const selectedBuildingId = ref(null);
const hoveredBuildingId = ref(null);
const hoveredStreet = ref(null);
const previewCategory = ref(null);
const probeVisible = ref(false);
const candidatePulse = ref(0);
const resultVersion = ref(0);
const showSuccess = ref(false);
const buttonRipple = ref(null);
const isRunning = ref(false);
const layers = ref({ area: true, streets: true, gaps: true, facilities: true });
let analysisTimer;
let successTimer;
let pointerFrame;
let pointerPosition;
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

const pending = computed(() =>
  Math.hypot(candidate.value.x - analysisOrigin.value.x, candidate.value.y - analysisOrigin.value.y) > 2,
);
const activeCategory = computed(() => categories.find((item) => item.id === selectedCategory.value));
const activeFacility = computed(() => facilities.find((item) => item.id === selectedFacilityId.value));
const activeBuilding = computed(() => buildingPois.find((item) => item.id === selectedBuildingId.value));
const displayCategory = computed(() => previewCategory.value ?? selectedCategory.value);
const displayCategoryData = computed(() => categories.find((item) => item.id === displayCategory.value));
const displayPoi = computed(() => {
  if (activeFacility.value) return { ...activeFacility.value, type: "facility", pinned: true };
  if (activeBuilding.value) return { ...activeBuilding.value, type: "building", pinned: true };
  const facility = facilities.find((item) => item.id === hoveredFacilityId.value);
  if (facility) return { ...facility, type: "facility", pinned: false };
  const building = buildingPois.find((item) => item.id === hoveredBuildingId.value);
  return building ? { ...building, type: "building", pinned: false } : null;
});
const probeKind = computed(() => isRunning.value ? "busy"
  : hoveredFacilityId.value ? "facility"
    : hoveredBuildingId.value ? "building"
      : hoveredStreet.value?.kind === "gap" ? "gap"
        : hoveredStreet.value ? "street" : "empty");
const probeLabel = computed(() => ({
  busy: "绘制中", facility: "查看设施", building: "查看建筑", gap: "沿街选点", street: "沿街选点", empty: "选起点",
})[probeKind.value]);
const visibleFacilities = computed(() =>
  facilities.filter((item) => mockWalkingMinutes(analysisOrigin.value, item) <= 15),
);
const categorySummary = computed(() =>
  categories.map((item) => {
    const count = visibleFacilities.value.filter((point) => point.category === item.id).length;
    const gap = Math.min(72, Math.max(8, item.baseGap + (2 - count) * 6));
    return { ...item, count, gap };
  }),
);
const selectedSummary = computed(() => categorySummary.value.find((item) => item.id === selectedCategory.value));
const areaPath = computed(() => {
  const { x, y } = analysisOrigin.value;
  return `M ${x - 194} ${y - 25}
    C ${x - 205} ${y - 108}, ${x - 140} ${y - 165}, ${x - 65} ${y - 167}
    C ${x + 27} ${y - 193}, ${x + 116} ${y - 155}, ${x + 153} ${y - 100}
    C ${x + 209} ${y - 48}, ${x + 200} ${y + 22}, ${x + 170} ${y + 86}
    C ${x + 135} ${y + 159}, ${x + 53} ${y + 178}, ${x - 35} ${y + 163}
    C ${x - 135} ${y + 172}, ${x - 205} ${y + 101}, ${x - 194} ${y - 25} Z`;
});

function choosePreset(point) {
  if (isRunning.value) return;
  candidate.value = { ...point };
  selectedFacilityId.value = null;
  selectedBuildingId.value = null;
  showSuccess.value = false;
  candidatePulse.value += 1;
}

function chooseMapPoint(event) {
  if (isRunning.value || !mapSvg.value) return;
  const matrix = mapSvg.value.getScreenCTM();
  if (!matrix) return;
  const point = mapSvg.value.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const local = point.matrixTransform(matrix.inverse());
  candidate.value = {
    id: "custom",
    label: "自选起点",
    hint: "地图选点",
    x: Math.round(Math.min(710, Math.max(190, local.x))),
    y: Math.round(Math.min(455, Math.max(160, local.y))),
  };
  selectedFacilityId.value = null;
  selectedBuildingId.value = null;
  showSuccess.value = false;
  candidatePulse.value += 1;
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

function moveProbe(event) {
  if (!motionEnabled(event) || !mapSvg.value) return;
  const bounds = mapSvg.value.getBoundingClientRect();
  pointerPosition = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  if (pointerFrame) return;
  pointerFrame = requestAnimationFrame(() => {
    pointerFrame = 0;
    if (probeEl.value && pointerPosition) {
      probeEl.value.style.transform = `translate3d(${pointerPosition.x}px, ${pointerPosition.y}px, 0)`;
    }
  });
}

function enterMap(event) {
  probeVisible.value = motionEnabled(event);
  moveProbe(event);
}

function leaveMap() {
  probeVisible.value = false;
  hoveredFacilityId.value = null;
  hoveredBuildingId.value = null;
  hoveredStreet.value = null;
  if (pointerFrame) cancelAnimationFrame(pointerFrame);
  pointerFrame = 0;
}

function hoverStreet(segment, kind) {
  hoveredStreet.value = { ...segment, kind };
  hoveredFacilityId.value = null;
  hoveredBuildingId.value = null;
}

function leaveStreet(id) {
  if (hoveredStreet.value?.id === id) hoveredStreet.value = null;
}

function hoverFacility(point) {
  hoveredFacilityId.value = point.id;
  hoveredBuildingId.value = null;
  hoveredStreet.value = null;
}

function hoverBuilding(building) {
  hoveredBuildingId.value = building.id;
  hoveredFacilityId.value = null;
  hoveredStreet.value = null;
}

function chooseBuilding(building) {
  selectedBuildingId.value = building.id;
  selectedFacilityId.value = null;
}

function moveMainButton(event) {
  if (!motionEnabled(event)) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = (event.clientX - rect.left - rect.width / 2) / (rect.width / 2);
  const y = (event.clientY - rect.top - rect.height / 2) / (rect.height / 2);
  event.currentTarget.style.setProperty("--magnet-x", `${(x * 4).toFixed(1)}px`);
  event.currentTarget.style.setProperty("--magnet-y", `${(y * 4).toFixed(1)}px`);
}

function resetMainButton(event) {
  event.currentTarget.style.setProperty("--magnet-x", "0px");
  event.currentTarget.style.setProperty("--magnet-y", "0px");
}

function runDemo(event) {
  if (isRunning.value) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const keyboardClick = event.detail === 0;
  buttonRipple.value = {
    id: Date.now(),
    x: keyboardClick ? rect.width / 2 : event.clientX - rect.left,
    y: keyboardClick ? rect.height / 2 : event.clientY - rect.top,
  };
  event.currentTarget.style.setProperty("--magnet-x", "0px");
  event.currentTarget.style.setProperty("--magnet-y", "0px");
  const nextOrigin = { ...candidate.value };
  isRunning.value = true;
  showSuccess.value = false;
  selectedFacilityId.value = null;
  selectedBuildingId.value = null;
  clearTimeout(analysisTimer);
  clearTimeout(successTimer);
  analysisTimer = setTimeout(() => {
    analysisOrigin.value = nextOrigin;
    resultVersion.value += 1;
    hoveredStreet.value = null;
    isRunning.value = false;
    showSuccess.value = true;
    successTimer = setTimeout(() => { showSuccess.value = false; }, 850);
  }, 700);
}

function chooseCategory(id) {
  selectedCategory.value = id;
  selectedFacilityId.value = null;
  selectedBuildingId.value = null;
  layers.value.gaps = true;
  layers.value.facilities = true;
}

function chooseFacility(point) {
  selectedFacilityId.value = point.id;
  selectedBuildingId.value = null;
  selectedCategory.value = point.category;
  previewCategory.value = null;
}

function closePoi() {
  selectedFacilityId.value = null;
  selectedBuildingId.value = null;
}

function toggleLayer(name) {
  layers.value[name] = !layers.value[name];
  if (name === "facilities" && !layers.value[name]) {
    hoveredFacilityId.value = null;
    selectedFacilityId.value = null;
  }
  if ((name === "streets" || name === "gaps") && !layers.value[name]) hoveredStreet.value = null;
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
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
  if (!delta) return;
  const scroller = event.target instanceof Element ? event.target.closest(".detail-scroll") : null;
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
  if (event.target instanceof Element && event.target.closest(".detail-scroll")) return;
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
  clearTimeout(analysisTimer);
  clearTimeout(successTimer);
  if (pointerFrame) cancelAnimationFrame(pointerFrame);
  if (wordmarkFrame) cancelAnimationFrame(wordmarkFrame);
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
        <span>上海 · 新江湾城样例区</span>
      </div>
      <div class="header-right">
        <button type="button" class="header-nav-button" @click="goToPage(1)">查看报告 <span aria-hidden="true">↓</span></button>
        <a class="header-contact" href="https://github.com/Sa4pphire" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 联系项目作者">联系 <span class="github-label">/ GitHub</span> <span aria-hidden="true">↗</span></a>
      </div>
    </header>

    <main class="dashboard">
      <section class="map-column" aria-label="生活圈地图">
        <div class="map-frame">
          <div class="map-topline">
            <span class="map-title"><span class="map-title-mark"></span>新江湾城 · 样例区步行地图 <small>合成数据示意</small></span>
            <button
              type="button"
              class="analyze-button map-analyze-button"
              :class="{ 'is-running': isRunning, 'is-complete': showSuccess }"
              :disabled="isRunning"
              @pointermove="moveMainButton"
              @pointerleave="resetMainButton"
              @click="runDemo"
            >
              <span v-if="buttonRipple" :key="buttonRipple.id" class="button-ripple" :style="{ left: `${buttonRipple.x}px`, top: `${buttonRipple.y}px` }" aria-hidden="true"></span>
              <span class="button-label">{{ isRunning ? "绘制示意中…" : showSuccess ? "已绘制示意结果" : pending ? "生成示意分析" : "重新生成示意分析" }}</span>
              <span class="button-arrow" aria-hidden="true">{{ showSuccess ? "✓" : isRunning ? "◌" : "↗" }}</span>
            </button>
          </div>

          <div class="map-canvas">
            <svg
              ref="mapSvg"
              class="demo-map"
              :class="{ 'is-running': isRunning }"
              viewBox="-90 0 1080 620"
              preserveAspectRatio="xMidYMid slice"
              role="group"
              aria-label="合成街区路网示意地图，可点击选择起点"
              @click="chooseMapPoint"
              @pointerenter="enterMap"
              @pointermove="moveProbe"
              @pointerleave="leaveMap"
              @keydown.esc.stop="closePoi"
            >
              <defs>
                <pattern id="small-grid" width="28" height="28" patternUnits="userSpaceOnUse">
                  <path d="M 28 0 L 0 0 0 28" fill="none" stroke="#DCE7E2" stroke-width="0.7" />
                </pattern>
                <clipPath id="reachable-clip"><path :d="areaPath" /></clipPath>
              </defs>

              <rect x="-90" width="1080" height="620" fill="#EAF0EC" />
              <rect x="-90" width="1080" height="620" fill="url(#small-grid)" opacity=".44" />

              <path class="waterway" d="M 0 62 C 79 83 80 163 59 250 S 38 417 0 480 V 62 Z" />
              <path class="water-edge" d="M 0 62 C 79 83 80 163 59 250 S 38 417 0 480" />
              <path class="park-area" d="M 226 66 H 366 V 126 H 226 Z M 468 327 H 604 V 432 H 468 Z M 688 75 H 788 V 125 H 688 Z" />
              <path class="park-path" d="M 239 98 C 287 81 322 111 353 93 M 486 384 Q 540 342 587 391 M 703 102 Q 742 81 777 108" />

              <g class="blocks">
                <path d="M 222 180 H 388 V 270 H 222 Z M 458 180 H 619 V 269 H 458 Z M 686 181 H 778 V 270 H 686 Z" />
                <path d="M 221 331 H 389 V 423 H 221 Z M 455 331 H 618 V 423 H 455 Z M 687 330 H 780 V 423 H 687 Z" />
                <path d="M 224 483 H 383 V 548 H 224 Z M 453 483 H 616 V 548 H 453 Z M 687 483 H 778 V 548 H 687 Z" />
              </g>
              <g class="building-lines">
                <path
                  v-for="building in buildingPois"
                  :key="building.id"
                  :d="building.d"
                  class="building-poi"
                  :class="{ hovered: hoveredBuildingId === building.id, selected: selectedBuildingId === building.id }"
                  role="button"
                  tabindex="0"
                  :aria-label="`${building.name}，点击查看示意信息`"
                  @pointerenter="hoverBuilding(building)"
                  @pointerleave="hoveredBuildingId = null"
                  @focus="hoverBuilding(building)"
                  @blur="hoveredBuildingId = null"
                  @click.stop="chooseBuilding(building)"
                  @keydown.enter.stop="chooseBuilding(building)"
                  @keydown.space.prevent.stop="chooseBuilding(building)"
                />
              </g>

              <g class="base-streets" aria-hidden="true">
                <path v-for="road in streets" :key="`${road.id}-base`" :d="road.d" class="road-edge" />
                <path v-for="road in streets" :key="`${road.id}-core`" :d="road.d" class="road-core" />
              </g>

              <g :key="resultVersion" class="result-visual" aria-hidden="true">
                <g v-if="layers.area" class="area-overlay">
                  <path :d="areaPath" class="reach-area" />
                  <path :d="areaPath" class="reach-outline" />
                </g>

                <g v-if="layers.streets" class="reach-overlay" clip-path="url(#reachable-clip)">
                  <path v-for="road in streets" :key="`${road.id}-reach`" :d="road.d" class="reach-street-halo" />
                  <path v-for="road in streets" :key="`${road.id}-reach-line`" :d="road.d" pathLength="1" class="reach-street" />
                </g>

                <g v-if="layers.gaps" :key="displayCategory" class="gap-overlay" clip-path="url(#reachable-clip)">
                  <path v-for="segment in gapStreets[displayCategory]" :key="`${segment.id}-halo`" :d="segment.d" class="gap-street-halo" />
                  <path v-for="segment in gapStreets[displayCategory]" :key="segment.id" :d="segment.d" class="gap-street" />
                </g>
              </g>

              <g class="map-labels" aria-hidden="true">
                <text x="256" y="106">社区绿地</text>
                <text x="507" y="377">公共开放空间</text>
                <text x="69" y="187" transform="rotate(-80 69 187)">滨水步道</text>
                <text x="695" y="531">街区 · 东</text>
              </g>

              <g v-if="hoveredStreet" clip-path="url(#reachable-clip)" aria-hidden="true" pointer-events="none">
                <path :d="hoveredStreet.d" class="street-hover-halo" :class="{ gap: hoveredStreet.kind === 'gap' }" />
                <path :d="hoveredStreet.d" class="street-hover-line" :class="{ gap: hoveredStreet.kind === 'gap' }" />
              </g>

              <g v-if="layers.streets" clip-path="url(#reachable-clip)" class="street-hit-areas" aria-hidden="true">
                <path
                  v-for="segment in interactiveStreets"
                  :key="segment.id"
                  :d="segment.d"
                  class="street-hit-area"
                  @pointerenter="hoverStreet(segment, 'reachable')"
                  @pointerleave="leaveStreet(segment.id)"
                />
              </g>
              <g v-if="layers.gaps" clip-path="url(#reachable-clip)" class="street-hit-areas gap-hit-areas" aria-hidden="true">
                <path
                  v-for="segment in gapStreets[displayCategory]"
                  :key="segment.id"
                  :d="segment.d"
                  class="street-hit-area gap-hit-area"
                  @pointerenter="hoverStreet(segment, 'gap')"
                  @pointerleave="leaveStreet(segment.id)"
                />
              </g>

              <g v-if="layers.facilities" class="facility-points">
                <g
                  v-for="point in facilities"
                  :key="point.id"
                  class="facility-marker"
                  :class="{ dimmed: displayCategory !== point.category, selected: selectedFacilityId === point.id, hovered: hoveredFacilityId === point.id }"
                  :transform="`translate(${point.x} ${point.y})`"
                  role="button"
                  tabindex="0"
                  :aria-label="`${point.name}，点击查看详情`"
                  @click.stop="chooseFacility(point)"
                  @pointerenter="hoverFacility(point)"
                  @pointerleave="hoveredFacilityId = null"
                  @focus="hoverFacility(point)"
                  @blur="hoveredFacilityId = null"
                  @keydown.enter.stop="chooseFacility(point)"
                  @keydown.space.prevent.stop="chooseFacility(point)"
                >
                  <circle r="15" class="facility-hit" />
                  <circle r="11" class="facility-circle" :stroke="categories.find((item) => item.id === point.category)?.color" />
                  <text y="1" class="facility-glyph">{{ categories.find((item) => item.id === point.category)?.short }}</text>
                </g>
              </g>

              <g class="origin-marker" :transform="`translate(${analysisOrigin.x} ${analysisOrigin.y})`" aria-hidden="true">
                <circle r="10" class="origin-halo" />
                <circle r="6" class="origin-dot" />
              </g>
              <g v-if="pending" class="candidate-marker" :transform="`translate(${candidate.x} ${candidate.y})`" aria-hidden="true">
                <circle r="10" class="candidate-halo" />
                <circle r="6" class="candidate-dot" />
              </g>
              <g v-if="candidatePulse" :key="candidatePulse" class="candidate-pulse" :transform="`translate(${candidate.x} ${candidate.y})`" aria-hidden="true">
                <circle r="10" />
              </g>
            </svg>

            <div ref="probeEl" class="map-probe" :class="[{ visible: probeVisible }, `mode-${probeKind}`]" aria-hidden="true">
              <span class="probe-ring"></span><span class="probe-caption">{{ probeLabel }}</span>
            </div>
            <div class="map-compass" aria-hidden="true"><span>北</span><i></i></div>
            <div v-if="hoveredStreet" class="street-hover-card" aria-live="polite">
              <small>合成街段 · {{ hoveredStreet.kind === 'gap' ? `${displayCategoryData.label}疑似灰段` : '示意可达街段' }}</small>
              <strong>{{ hoveredStreet.name }}</strong>
            </div>
            <div class="map-legend" aria-label="地图图例">
              <span><i class="legend-swatch area"></i>近似可达面</span>
              <span><i class="legend-swatch route"></i>可达街段</span>
              <span><i class="legend-swatch gap"></i>{{ displayCategoryData.label }}疑似灰段 <em v-if="previewCategory && previewCategory !== selectedCategory">预览</em></span>
            </div>

            <div v-if="displayPoi" class="facility-popover">
              <button v-if="displayPoi.pinned" type="button" class="popover-close" aria-label="关闭 POI 信息" @click="closePoi">×</button>
              <span class="popover-type">{{ displayPoi.pinned ? '已固定 · ' : '悬停预览 · ' }}{{ displayPoi.type === 'facility' ? categories.find((item) => item.id === displayPoi.category)?.label : '合成建筑' }}</span>
              <strong>{{ displayPoi.name }}</strong>
              <span v-if="displayPoi.type === 'facility'">起点至此约 {{ mockWalkingMinutes(analysisOrigin, displayPoi) }} 分钟 <em>（合成示意）</em></span>
              <span v-else>仅展示建筑轮廓，不参与设施覆盖计算。</span>
            </div>
          </div>

          <div class="map-bottomline">
            <span><span class="line-signal"></span>{{ pending ? "新起点待分析 · 点击右上角生成" : "已显示当前起点的示意结果" }}</span>
            <span>合成数据 · 向下滚动查看详情</span>
          </div>
        </div>
      </section>
    </main>
    <footer class="living-footer" aria-label="LIVING CIRCLE">
      <div class="living-meta" aria-hidden="true">
        <span>15 MIN WALKABILITY / XINJIANGWANCHENG</span>
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
    </footer>
    </section>

    <section ref="detailPage" class="book-page detail-page" aria-label="生活圈控制面板">
      <div class="detail-page-inner">
        <div class="detail-page-heading">
          <div>
            <p class="section-kicker">15 分钟生活圈 · 示意体检</p>
            <h2>从地图走进街区细节</h2>
          </div>
          <button type="button" class="return-map-button" @click="goToPage(0)">返回地图 <span aria-hidden="true">↑</span></button>
        </div>
        <div class="detail-scroll">

      <aside class="insight-panel" aria-label="选点与覆盖分析">
        <div class="demo-warning">
          <span class="warning-icon">!</span>
          <span><strong>前端演示模式</strong> · 当前结果仅用于界面与交互评估，不是真实路网分析。</span>
        </div>

        <section class="panel-section origin-section">
          <div class="section-head"><span class="section-index">01</span><h2>选择起点</h2></div>
          <p class="section-explain">点击示意地图选择起点，或选择一个预设位置。</p>
          <div class="preset-list">
            <button
              v-for="point in presets"
              :key="point.id"
              type="button"
              class="preset-button"
              :class="{ active: candidate.id === point.id }"
              :aria-pressed="candidate.id === point.id"
              :disabled="isRunning"
              @click="choosePreset(point)"
            >
              <span>{{ point.label }}</span><small>{{ point.hint }}</small>
            </button>
          </div>
          <div class="selected-origin">
            <span class="origin-pin" aria-hidden="true"></span>
            <div><small>当前选择</small><strong>{{ candidate.label }} <span>{{ candidate.hint }}</span></strong></div>
            <code>{{ candidate.x }}, {{ candidate.y }}</code>
          </div>
          <p class="origin-action-hint">选好预设点后，返回地图点击右上角按钮生成结果。</p>
        </section>

        <section class="panel-section summary-section">
          <div class="section-head"><span class="section-index">02</span><h2>当前生活圈</h2></div>
          <div class="summary-band">
            <div class="summary-primary"><strong>15<span>分钟</span></strong><small>示意步行范围</small></div>
            <div class="summary-secondary"><strong :key="resultVersion" class="summary-number">{{ visibleFacilities.length }}<span> / {{ facilities.length }}</span></strong><small>圈内合成设施点</small></div>
          </div>
          <div class="layer-controls" aria-label="地图图层">
            <button type="button" :aria-pressed="layers.area" :class="{ on: layers.area }" @click="toggleLayer('area')"><i class="layer-dot area"></i>近似面</button>
            <button type="button" :aria-pressed="layers.streets" :class="{ on: layers.streets }" @click="toggleLayer('streets')"><i class="layer-dot route"></i>街段</button>
            <button type="button" :aria-pressed="layers.gaps" :class="{ on: layers.gaps }" @click="toggleLayer('gaps')"><i class="layer-dot gap"></i>灰段</button>
            <button type="button" :aria-pressed="layers.facilities" :class="{ on: layers.facilities }" @click="toggleLayer('facilities')"><i class="layer-dot point"></i>设施</button>
          </div>
        </section>

        <section class="panel-section service-section">
          <div class="section-head"><span class="section-index">03</span><h2>按服务查看</h2><span class="section-side-note">点击切换地图重点</span></div>
          <div class="service-list">
            <button
              v-for="item in categorySummary"
              :key="item.id"
              type="button"
              class="service-row"
              :class="{ active: selectedCategory === item.id, preview: previewCategory === item.id && selectedCategory !== item.id }"
              :aria-pressed="selectedCategory === item.id"
              @pointerenter="previewCategory = item.id"
              @pointerleave="previewCategory = null"
              @focus="previewCategory = item.id"
              @blur="previewCategory = null"
              @click="chooseCategory(item.id)"
            >
              <span class="service-icon" :style="{ '--category-color': item.color }">{{ item.short }}</span>
              <span class="service-main"><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
              <span class="service-values"><strong>{{ item.count }} <small>处</small></strong><small>示意灰段 {{ item.gap }}%</small></span>
              <span class="row-chevron" aria-hidden="true">›</span>
            </button>
          </div>
          <div class="service-insight">
            <span class="insight-accent"></span>
            <p><strong>{{ activeCategory.label }}</strong>：圈内示意设施 {{ selectedSummary.count }} 处；当前高亮的橙色街段表示该类服务的<strong>疑似覆盖缺口</strong>。</p>
          </div>
        </section>

        <div class="panel-footer">真实版将接入 Python API、C++ 路网结果与核查后的设施数据。</div>
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
