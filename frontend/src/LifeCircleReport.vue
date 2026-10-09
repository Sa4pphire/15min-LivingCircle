<script setup>
import { computed, onUnmounted, ref, useId, watch } from 'vue';
import { fitLocalPoints, geometryToSvgPath } from './mapGeometry.js';
import { createLocalBd09Alignment } from './mapAsset.js';
import { lifeCategories, geometryAreaMeters, nearbyPlaces, placeWalkingSeconds,
  resultMeterGeometry, serviceReminder, simplePlaceStatus } from './lifeCircleSummary.js';
import './lifeCircleReport.css';

const props = defineProps({ region: { type: Object, required: true }, mode: { type: String, required: true },
  result: { type: Object, default: null }, state: { type: String, default: 'idle' },
  origin: { type: Object, default: null }, route: { type: Object, default: null }, active: Boolean });
const emit = defineEmits(['map', 'mode', 'select', 'services']);
const category = ref('all'), query = ref(''), selectedId = ref(null), limit = ref(8);
const showHelp = ref(false), animatedCount = ref(0);
const figureId = useId();
const regionTitle = computed(() => String(props.region.name || '当前区域').replace(/路网演示区$/, '') || '当前区域');
let countFrame;
const places = computed(() => nearbyPlaces(props.result));
const hasPlaceCount = computed(() => places.value.length > 0 || ['ready', 'partial'].includes(props.result?.poiInfo?.status));
const filtered = computed(() => nearbyPlaces(props.result, category.value, query.value));
const selectedPlace = computed(() => filtered.value.find(poi => poi.id === selectedId.value));
const categoryRows = computed(() => lifeCategories.map(item => ({ ...item,
  count: places.value.filter(poi => (poi.categories ?? [poi.category]).includes(item.id)).length })));
const geometry = computed(() => resultMeterGeometry(props.result));
const area = computed(() => geometryAreaMeters(geometry.value));
const areaLabel = computed(() => area.value === null ? '范围暂未生成' : area.value < 10_000
  ? '小于 0.01 平方公里' : `约 ${(area.value / 1_000_000).toFixed(2)} 平方公里`);
const loading = computed(() => props.state === 'running');
const reminder = computed(() => props.mode === 'real' ? serviceReminder(props.result) : null);
const alignment = computed(() => props.region.alignment ? createLocalBd09Alignment(props.region.alignment) : null);
const figure = computed(() => {
  if (!area.value) return null;
  const polygons = geometry.value.type === 'Polygon' ? [geometry.value.coordinates] : geometry.value.coordinates;
  const points = polygons.flat(2);
  const fit = fitLocalPoints(points, 480, 320, .09);
  const project = point => [fit.translateX + point[0] * fit.scale, fit.translateY + point[1] * fit.scale];
  return { d: geometryToSvgPath(geometry.value, project), project };
});
const figurePlaces = computed(() => {
  if (!figure.value) return [];
  const shown = filtered.value.slice(0, 100);
  if (selectedPlace.value && !shown.some(poi => poi.id === selectedPlace.value.id)) shown.push(selectedPlace.value);
  return shown.flatMap(poi => {
    const point = poi.point ?? (poi.bd09 ? alignment.value?.toLocal(poi.bd09) : null);
    if (!Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite)) return [];
    const pixel = figure.value.project(point);
    return [{ ...poi, pixel, color: lifeCategories.find(item => item.id === poi.category)?.color ?? '#147d72' }];
  });
});
const figureOrigin = computed(() => figure.value && props.origin?.local
  ? figure.value.project([props.origin.local.x, props.origin.local.y]) : null);
const selectedSeconds = computed(() => selectedPlace.value
  ? placeWalkingSeconds(props.result, selectedPlace.value, props.route) : null);
const walkingLabel = poi => {
  const seconds = placeWalkingSeconds(props.result, poi, props.route);
  return seconds === null ? '查看路线' : seconds < 60 ? '不到 1 分钟' : `约 ${Math.ceil(seconds / 60)} 分钟`;
};
function select(poi) { selectedId.value = selectedId.value === poi.id ? null : poi.id; }
watch([() => props.mode, () => props.result, category, query], () => {
  limit.value = 8;
  if (!filtered.value.some(poi => poi.id === selectedId.value)) selectedId.value = null;
});
watch(() => props.mode, () => { category.value = 'all'; query.value = ''; selectedId.value = null; showHelp.value = false; });
watch([() => props.active, () => props.mode, () => places.value.length], () => {
  cancelAnimationFrame(countFrame);
  const target = places.value.length;
  if (!props.active || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    animatedCount.value = target; return;
  }
  const start = performance.now(); animatedCount.value = 0;
  const step = time => {
    const progress = Math.min(1, (time - start) / 600);
    animatedCount.value = Math.round(target * (1 - (1 - progress) ** 3));
    if (progress < 1 && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) countFrame = requestAnimationFrame(step);
    else animatedCount.value = target;
  };
  countFrame = requestAnimationFrame(step);
}, { immediate: true });
onUnmounted(() => cancelAnimationFrame(countFrame));
</script>

<template>
  <div class="life-circle-report" :class="{ 'is-active': active }">
    <header class="lcr-header">
      <div><p>{{ regionTitle }}</p><h2>你的 15 分钟生活圈</h2></div>
      <div class="lcr-header-actions">
        <div class="lcr-mode-switch" role="group" aria-label="结果计算方式">
          <button type="button" :aria-pressed="mode === 'real'" @click="emit('mode', 'real')">在线计算</button>
          <button type="button" :aria-pressed="mode === 'synthetic'" @click="emit('mode', 'synthetic')">合成模拟</button>
        </div>
        <button class="lcr-map-button" type="button" @click="emit('map')">返回地图 <span aria-hidden="true">↑</span></button>
      </div>
    </header>
    <div class="detail-scroll lcr-scroll">
      <Transition name="lcr-content" mode="out-in">
        <div v-if="!result" :key="`${mode}-${state}`" class="lcr-empty" :aria-busy="loading">
          <span class="lcr-empty-symbol" :class="{ 'is-loading': loading }" aria-hidden="true"><i></i></span>
          <h3>{{ loading ? '正在看看，你能走到哪里。' : state === 'error' ? '这次没有算出来。' : '选一个起点，看看附近的生活。' }}</h3>
          <p role="status">{{ loading ? '范围和附近地点准备好后，会显示在这里。' : state === 'error' ? '回到地图，再试一次。' : origin ? '起点已经选好，回到地图点击“计算 15 分钟范围”。' : '先在地图上选点，再点击“计算 15 分钟范围”。' }}</p>
          <button v-if="!loading" class="lcr-primary-button" type="button" @click="emit('map')">{{ origin ? '回到地图计算' : '去地图选起点' }}</button>
          <p class="lcr-empty-note">{{ mode === 'synthetic' ? '模拟结果仅供体验，实际出行请参考在线计算。' : '从当前位置出发，寻找身边的日常服务。' }}</p>
        </div>
        <div v-else :key="`${mode}-result`" class="lcr-results">
          <section class="lcr-overview" aria-label="步行范围结果">
            <div class="lcr-overview-title"><h3>从这里出发</h3><span>步行约 15 分钟</span></div>
            <button type="button" class="lcr-figure-button" aria-label="在地图上查看步行范围" @click="emit('map')">
              <svg v-if="figure" class="lcr-figure" viewBox="0 0 480 320" aria-hidden="true">
                <defs><clipPath :id="`${figureId}-area`"><path :d="figure.d" clip-rule="evenodd"/></clipPath></defs>
                <path :d="figure.d" class="lcr-shape-fill" fill-rule="evenodd"/>
                <path :d="figure.d" class="lcr-shape-line" pathLength="1" fill-rule="evenodd"/>
                <g :clip-path="`url(#${figureId}-area)`">
                  <circle v-for="poi in figurePlaces" :key="poi.id" :cx="poi.pixel[0]" :cy="poi.pixel[1]" :r="selectedId === poi.id ? 7 : 4.5"
                    :fill="poi.color" stroke="#fbfdfc" stroke-width="2"/>
                </g>
                <g v-if="figureOrigin" :transform="`translate(${figureOrigin[0]} ${figureOrigin[1]})`"><circle r="11" fill="#fbfdfc"/><circle r="5" fill="#123c36"/><path d="M0-9V-18" stroke="#123c36" stroke-width="2"/></g>
              </svg>
              <span v-else class="lcr-figure-missing">这次还没有生成步行范围。</span>
              <span class="lcr-figure-caption">点这里，回地图看看 <span aria-hidden="true">↗</span></span>
            </button>
            <div class="lcr-overview-facts"><div><strong>{{ areaLabel }}</strong><span>这次计算的范围</span></div>
              <div><strong v-if="hasPlaceCount"><span class="lcr-place-total" :aria-label="`找到 ${places.length} 处地点`">{{ animatedCount }}</span> 处地点</strong><strong v-else>地点信息待补充</strong><span>在这个范围内找到</span></div></div>
            <p class="lcr-mode-note">{{ mode === 'synthetic' ? '这是模拟结果，仅供体验。' : '范围仅供参考，具体走法可查看地点路线。' }}</p>
            <div v-if="reminder" class="lcr-service-reminder"><h4>附近服务提醒</h4><p>{{ reminder }}</p><button type="button" @click="emit('services')">在地图上看看 <span aria-hidden="true">↗</span></button></div>
            <div class="lcr-help"><button type="button" :aria-expanded="showHelp" :aria-controls="`${figureId}-help`" @click="showHelp = !showHelp">这张图怎么看？<span aria-hidden="true">{{ showHelp ? '−' : '+' }}</span></button>
              <Transition name="lcr-detail"><p v-if="showHelp" :id="`${figureId}-help`">绿色是参考步行范围，彩色点是附近地点。选一个类别，再点开地点，就能查看路线。</p></Transition></div>
          </section>
          <section class="lcr-places" aria-labelledby="lcr-places-title">
            <div class="lcr-places-heading"><h3 id="lcr-places-title">附近有什么？</h3><span>{{ hasPlaceCount ? `${places.length} 处已找到的地点` : '地点信息待补充' }}</span></div>
            <p class="lcr-place-status" role="status">{{ simplePlaceStatus(result, state) }}</p>
            <div class="lcr-categories" role="group" aria-label="选择地点类别">
              <button type="button" :aria-pressed="category === 'all'" @click="category = 'all'">全部 <b>{{ hasPlaceCount ? places.length : '—' }}</b></button>
              <button v-for="item in categoryRows" :key="item.id" type="button" :style="{ '--category-color': item.color }" :aria-pressed="category === item.id" @click="category = item.id">{{ item.label }} <b>{{ hasPlaceCount ? item.count : '—' }}</b></button>
            </div>
            <label class="lcr-search"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg><input v-model="query" type="search" placeholder="搜索地点名称或地址" aria-label="搜索附近地点"/></label>
            <Transition name="lcr-list" mode="out-in">
              <div :key="`${category}-${query}`" class="lcr-list-content">
                <ul v-if="filtered.length" class="lcr-place-list">
                  <li v-for="poi in filtered.slice(0, limit)" :key="poi.id">
                    <button type="button" :aria-pressed="selectedId === poi.id" :aria-label="`查看${poi.name || '这个地点'}的详情`" @click="select(poi)">
                      <span class="lcr-place-glyph" :style="{ '--category-color': lifeCategories.find(item => item.id === poi.category)?.color }">{{ lifeCategories.find(item => item.id === poi.category)?.glyph }}</span>
                      <span class="lcr-place-name"><strong>{{ poi.name || '未命名地点' }}</strong><small>{{ poi.address || '点开看看怎么走' }}</small></span>
                      <span class="lcr-place-time">{{ walkingLabel(poi) }}<span aria-hidden="true">{{ selectedId === poi.id ? '−' : '+' }}</span></span>
                    </button>
                    <Transition name="lcr-detail">
                      <div v-if="selectedId === poi.id" class="lcr-detail-shell"><div class="lcr-detail-inner"><div class="lcr-place-detail">
                        <p>{{ selectedSeconds === null ? '到地图上查看从起点出发的步行路线。' : selectedSeconds < 60 ? '从起点出发，预计不到 1 分钟。' : `从起点出发，预计走 ${Math.ceil(selectedSeconds / 60)} 分钟。` }}<span v-if="selectedSeconds > 900">这条路线超过 15 分钟。</span></p>
                        <p v-if="mode === 'synthetic'" class="lcr-detail-note">模拟路线仅供参考。</p>
                        <button type="button" class="lcr-primary-button" @click="emit('select', poi)">到地图查看路线 <span aria-hidden="true">↗</span></button>
                      </div></div></div>
                    </Transition>
                  </li>
                </ul>
                <p v-else class="lcr-no-places" role="status">{{ query ? '没有找到匹配的地点，试试其他名称。' : state === 'enriching' ? '正在补充地点，请稍等。' : category === 'all' ? '暂时没有地点可显示。不代表周边没有，可以换个起点试试。' : '这类地点暂未显示，换个类别看看。' }}</p>
                <button v-if="filtered.length > limit" type="button" class="lcr-more" @click="limit += 8">查看更多地点 <span>{{ Math.min(limit, filtered.length) }} / {{ filtered.length }}</span></button>
              </div>
            </Transition>
            <p class="lcr-result-note">{{ mode === 'synthetic' ? '模拟结果用于了解附近的步行范围，实际出行请参考在线计算。' : '地图内的地点不一定都能在 15 分钟内走到，请以具体路线时间为准。' }}</p>
          </section>
        </div>
      </Transition>
    </div>
  </div>
</template>
