<script setup>
import { computed, ref, watch } from "vue";
import { poiAccessLabel, poiCacheLabel, poiCategoryCounts, poiCategoryStyles, poiEmptyLabel, poiInfo, poiSearchProgress, visiblePois } from "./poiFacilities.js";

const props = defineProps({ result: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  compact: { type: Boolean, default: false }, category: { type: String, default: "all" } });
const emit = defineEmits(["category", "select", "refresh"]);
const selected = ref(null);
const info = computed(() => poiInfo(props.result));
const entries = computed(() => visiblePois(props.result, props.category));
const categoryCounts = computed(() => poiCategoryCounts(props.result));
const count = (key) => visiblePois(props.result, key).length;
const status = computed(() => !info.value ? "尚未加载设施" :
  info.value.status === "pending" ? "等时圈已显示，正在补充设施" :
  info.value.cacheOnly && info.value.refreshRequired ? "本地设施缓存不足；可刷新 POI 或先预采集，不影响等时圈" :
  info.value.status === "unavailable" ? "设施检索不可用，请检查后端服务端 AK 与接口权限" :
    info.value.stalePages ? "使用旧缓存，设施信息待更新" :
      info.value.status === "partial" ? "检索清单不完整，不能据此判定设施匮乏" : "百度在线候选 POI · 非核实入口清单");
watch([() => props.result, () => props.category], () => { selected.value = null; });
</script>

<template>
  <section class="poi-inventory" :class="{ compact }" aria-label="百度设施候选清单">
    <div v-if="compact" class="poi-compact-heading">
      <strong>圈内 POI</strong>
      <span role="status">{{ poiCacheLabel(result) }}</span>
    </div>
    <div class="poi-heading" v-if="!compact">
      <h3>近似等时圈面内设施</h3>
      <button type="button" :disabled="busy || info?.status === 'pending'" @click="emit('refresh')">{{ busy ? '设施更新中…' : '刷新 POI（调用 API）' }}</button>
    </div>
    <div class="poi-filters" role="group" aria-label="设施类别筛选">
      <button type="button" :aria-pressed="category === 'all'" @click="emit('category', 'all')">全部 <b>{{ count('all') }}</b></button>
      <button v-for="(style, key) in poiCategoryStyles" :key="key" type="button"
        :style="{ '--poi-color': style.color }" :aria-pressed="category === key" @click="emit('category', key)">
        <span aria-hidden="true">{{ style.glyph }}</span>{{ style.label }} <b>{{ count(key) }}</b>
      </button>
    </div>
    <p v-if="!compact" class="poi-status" role="status">{{ status }}</p>
    <p v-if="poiSearchProgress(result)" class="poi-status" role="status">{{ poiSearchProgress(result) }}</p>
    <p v-if="compact && (!entries.length || info?.status === 'unavailable')" class="poi-empty" role="status">{{ poiEmptyLabel(result, category) }}</p>
    <p v-if="!compact && info" class="poi-cache-status">本次百度请求 {{ info.apiRequests ?? 0 }} 次，缓存命中 {{ info.cacheHits ?? 0 }} 项；缓存有效期 {{ info.cacheTtlHours ?? 168 }} 小时。</p>
    <template v-if="!compact">
      <p class="poi-explanation">圈内数量按近似展示面统计，不等于步行可达数量。只有导航点接入路网的设施才有 C++ 模型耗时；清单和入口未经核实，不生成真实匮乏结论。</p>
      <table class="poi-counts" aria-label="POI 候选点位分类统计">
        <thead><tr><th scope="col">类别</th><th scope="col">检索候选</th><th scope="col">圈内</th><th scope="col">模型可达*</th></tr></thead>
        <tbody><tr v-for="row in categoryCounts" :key="row.category">
          <th scope="row" :style="{ color: row.color }">{{ row.label }}</th>
          <td>{{ row.queried ?? '—' }}</td><td>{{ row.displayed }}</td><td>{{ row.modelReachable }}</td>
        </tr></tbody>
      </table>
      <p class="poi-explanation">*仅统计当前显示点位中的模型可达候选。学校门口等独立 POI 会单独计数，不等于学校或机构数量。</p>
      <p v-if="entries.length" class="poi-explanation">点击设施名称，返回地图定位并查看详情。</p>
      <ul v-if="entries.length" class="poi-list">
        <li v-for="poi in entries" :key="poi.id">
          <button type="button" :aria-label="`在地图上查看${poi.name}`" @click="selected = poi; emit('select', poi)">
            <span class="poi-list-glyph" :style="{ color: poiCategoryStyles[poi.category].color }">{{ poiCategoryStyles[poi.category].glyph }}</span>
            <span><strong>{{ poi.name }}</strong><small>{{ poiAccessLabel(poi) }}</small><small v-if="poi.address">{{ poi.address }}</small></span>
          </button>
        </li>
      </ul>
      <p v-else class="poi-empty">{{ poiEmptyLabel(result, category) }}</p>
      <p v-if="selected" class="poi-explanation" role="status">{{ selected.name }}：<span v-if="selected.bd09?.length === 2">原始 BD-09 点位 {{ selected.bd09[0].toFixed(6) }}, {{ selected.bd09[1].toFixed(6) }}。</span>{{ poiAccessLabel(selected) }}</p>
    </template>
  </section>
</template>

<style scoped>
.poi-inventory { color: #173c3a; text-align: left; }
.poi-heading { display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; justify-content: space-between; }
.poi-heading h3 { margin: 0 0 12px; font-size: 16px; }
.poi-heading > button { border: 0; background: transparent; color: #316e61; font: inherit; font-size: 11px; cursor: pointer; text-decoration: underline; }
.poi-heading > button:disabled { opacity: .55; cursor: wait; }
.poi-compact-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 9px; margin-bottom: 6px; font-size: 10px; line-height: 1.5; }
.poi-compact-heading span { color: #5a756b; }
.poi-filters { display: flex; flex-wrap: wrap; gap: 6px; }
.poi-filters button { display: inline-flex; gap: 5px; align-items: center; border: 1px solid #c9dcd4; background: #fbfdfb; color: #385a50; padding: 7px 9px; font: inherit; font-size: 11px; cursor: pointer; }
.poi-filters button[aria-pressed="true"] { border-color: #147d72; box-shadow: inset 0 -2px #147d72; }
.poi-filters button span { color: var(--poi-color); font-weight: 700; }
.poi-filters b { font-variant-numeric: tabular-nums; }
.poi-filters button:focus-visible, .poi-list button:focus-visible { outline: 2px solid #2f6e91; outline-offset: 2px; }
.poi-status, .poi-cache-status, .poi-explanation, .poi-empty { font-size: 11px; line-height: 1.7; margin: 10px 0; }
.poi-status { color: #795d18; }
.poi-cache-status, .poi-explanation { color: #5a756b; }
.poi-counts { width: 100%; border-collapse: collapse; font-size: 11px; font-variant-numeric: tabular-nums; }
.poi-counts th, .poi-counts td { padding: 7px 3px; border-bottom: 1px solid #dce8e1; text-align: right; }
.poi-counts th:first-child { text-align: left; }
.poi-counts thead th { color: #5a756b; font-size: 10px; font-weight: 500; }
.poi-list { list-style: none; padding: 0; margin: 10px 0 0; max-height: 260px; overflow: auto; }
.poi-list li { border-top: 1px solid #dce8e1; }
.poi-list button { display: flex; align-items: flex-start; gap: 11px; width: 100%; padding: 11px 0; border: 0; background: transparent; text-align: left; color: inherit; font: inherit; cursor: pointer; }
.poi-list-glyph { padding-top: 1px; font-weight: 700; }
.poi-list strong { display: block; font-size: 12px; }
.poi-list small { display: block; margin-top: 3px; font-size: 10px; line-height: 1.5; color: #5a756b; }
.compact { padding: 7px; background: rgba(251, 253, 251, .96); }
.compact .poi-empty { max-width: 245px; font-size: 10px; margin: 6px 0 0; }
.compact .poi-status { max-width: 440px; font-size: 10px; margin: 6px 0 0; }
@media (max-width: 600px) { .poi-filters button { padding: 6px; font-size: 10px; } }
</style>
