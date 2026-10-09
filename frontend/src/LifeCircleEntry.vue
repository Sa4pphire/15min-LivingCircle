<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import { activateRegion, readDisplayRegion } from './regionLoader.js';
import './regionEntry.css';

const packages = ref([]), selected = ref(''), opened = shallowRef(null);
const preview = shallowRef(null), viewComponent = shallowRef(null);
const loading = ref(true), previewLoading = ref(false), opening = ref(false), error = ref('');
const cache = new Map();
let previewController, listController, selectionSequence = 0, disposed = false;
let viewImport;
const selectedPackage = computed(() => packages.value.find(item => item.id === selected.value));
const availableCount = computed(() => packages.value.filter(item => item.displayReady).length);
const previewGroups = computed(() => {
  const groups = {};
  for (const feature of preview.value?.context.features ?? []) {
    (groups[feature.kind] ??= []).push(feature);
  }
  return groups;
});
const previewBox = computed(() => {
  const bounds = preview.value?.displayBounds;
  if (!bounds) return '0 0 100 100';
  const width = bounds.maxX - bounds.minX, height = bounds.maxY - bounds.minY;
  const padding = Math.max(width, height) * .045;
  return `${bounds.minX - padding} ${bounds.minY - padding} ${width + padding * 2} ${height + padding * 2}`;
});
const previewExtent = computed(() => {
  const b = preview.value?.displayBounds;
  return b ? { x: b.minX, y: b.minY, width: b.maxX - b.minX, height: b.maxY - b.minY } : null;
});
function size(item) {
  const b = item?.boundsMeters;
  return b ? `${((b.maxX - b.minX) / 1000).toFixed(2)} × ${((b.maxY - b.minY) / 1000).toFixed(2)} 公里` : '';
}
function warmView() {
  viewImport ??= import('./App.vue');
  return viewImport.catch(exc => { viewImport = null; throw exc; });
}
async function loadPreview(identity) {
  previewController?.abort();
  const sequence = ++selectionSequence;
  const controller = new AbortController();
  previewController = controller;
  preview.value = null; error.value = ''; previewLoading.value = Boolean(identity);
  if (!identity) return;
  try {
    const region = cache.get(identity) ?? await readDisplayRegion(identity, fetch, { signal: controller.signal });
    if (disposed || controller.signal.aborted || sequence !== selectionSequence) return;
    cache.set(identity, region); preview.value = region;
  } catch (exc) {
    if (!controller.signal.aborted && sequence === selectionSequence) error.value = `区域预览无法加载：${exc.message}`;
  } finally {
    if (sequence === selectionSequence) previewLoading.value = false;
  }
}
async function list() {
  listController?.abort();
  const controller = new AbortController(); listController = controller;
  loading.value = true; error.value = '';
  try {
    const response = await fetch('/api/v1/regions', { cache: 'no-cache', signal: controller.signal });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.items)) throw new Error(data.detail?.message || '区域列表暂时不可用');
    if (disposed || controller.signal.aborted) return;
    packages.value = data.items;
    const preferred = new URLSearchParams(location.search).get('region') || selected.value || data.defaultRegionId;
    selected.value = data.items.find(item => item.id === preferred && item.displayReady)?.id
      || data.items.find(item => item.displayReady)?.id || '';
    cache.clear();
    if (selected.value) await loadPreview(selected.value);
  } catch (exc) {
    if (!controller.signal.aborted) error.value = `无法读取区域列表：${exc.message}。请确认后端已启动后重试。`;
  } finally { if (!controller.signal.aborted) loading.value = false; }
}
async function enter() {
  if (opening.value || loading.value || previewLoading.value || !selectedPackage.value?.displayReady || !preview.value) return;
  opening.value = true; error.value = '';
  try {
    const { default: App } = await warmView();
    if (disposed) return;
    // App and its map capture this package in setup; activate before mounting.
    activateRegion(preview.value);
    viewComponent.value = App;
    const url = new URL(location.href); url.searchParams.set('region', preview.value.id);
    history.replaceState(null, '', url);
    opened.value = preview.value;
  } catch (exc) { error.value = `展示页无法打开：${exc.message}。请重试。`; }
  finally { opening.value = false; }
}
function chooseRegion() { opened.value = null; }
async function onEntered() {
  await nextTick();
  // Focus follows the scene without scrolling; the map observes its own size.
  const heading = document.querySelector(opened.value ? '.life-region-view .brand-title' : '.region-library-title');
  heading?.setAttribute('tabindex', '-1'); heading?.focus({ preventScroll: true });
}
watch(selected, identity => { if (!loading.value) void loadPreview(identity); });
onMounted(() => { void list(); void warmView().catch(() => {}); });
onUnmounted(() => { disposed = true; previewController?.abort(); listController?.abort(); });
</script>

<template>
  <div class="life-entry">
    <Transition name="region-scene" mode="out-in" @after-enter="onEntered">
      <section v-if="opened" :key="`view-${opened.id}`" class="life-region-view">
        <component :is="viewComponent" :key="opened.id" @choose-region="chooseRegion" />
      </section>
      <main v-else key="library" class="region-library" :aria-busy="loading || opening">
        <header class="region-library-header">
          <div class="region-library-brand">
            <svg viewBox="0 0 36 36" aria-hidden="true" fill="none"><circle cx="18" cy="18" r="13.5" stroke="currentColor" stroke-width="1.5"/><circle cx="18" cy="18" r="8.5" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3"/><circle cx="18" cy="18" r="3.2" fill="currentColor"/></svg>
            <span>步行生活圈<small>15 分钟可达性体检</small></span>
          </div>
          <a class="region-editor-link" href="/?mode=editor">路网编辑器</a>
        </header>
        <div class="region-library-body">
          <section class="region-library-choice" aria-labelledby="region-library-title">
            <div class="region-library-heading">
              <h1 id="region-library-title" class="region-library-title">选择一片区域，<br>看见日常的步行范围。</h1>
              <p>打开已有区域包，从地图上的一个起点开始探索。</p>
            </div>
            <div class="region-list-heading"><h2>展示区域</h2><span v-if="!loading">{{ availableCount }} 个可用</span>
              <button type="button" :disabled="loading || opening" @click="list">{{ loading ? '读取中…' : '刷新' }}</button></div>
            <fieldset class="region-package-list" :disabled="opening || loading">
              <legend class="region-visually-hidden">选择生活圈展示区域</legend>
              <p v-if="loading" class="region-list-message" role="status">正在读取区域包…</p>
              <p v-else-if="!packages.length" class="region-list-message">暂无区域包，请先在路网编辑器中创建或导入。</p>
              <label v-for="item in packages" :key="item.id" class="region-package-row"
                :class="{ 'is-selected': selected === item.id, 'is-unavailable': !item.displayReady }">
                <input v-model="selected" type="radio" name="display-region" :value="item.id" :disabled="!item.displayReady">
                <span class="region-package-content"><strong>{{ item.name }}<span v-if="!item.displayReady" class="region-package-badge">{{ item.error ? '数据异常' : '路网待完成' }}</span></strong>
                  <small v-if="item.displayReady">{{ size(item) }}<span>版本 {{ item.version }}</span></small>
                  <small v-else>{{ item.unavailableReason }}</small></span>
                <span v-if="selected === item.id" class="region-package-check" aria-hidden="true">✓</span>
              </label>
            </fieldset>
            <p v-if="error" class="region-library-error" role="alert">{{ error }}<button v-if="selected && !preview && !loading" type="button" @click="loadPreview(selected)">重新加载</button></p>
            <div class="region-entry-action">
              <p>{{ selectedPackage?.displayReady ? '区域数据准备好后，即可进入地图。' : '完成区域路网后，即可在这里展示。' }}</p>
              <button type="button" class="region-enter-button" :disabled="loading || previewLoading || opening || !preview || !selectedPackage?.displayReady" @click="enter">
                {{ opening ? '正在进入…' : previewLoading ? '正在准备区域…' : '进入生活圈' }}
                <svg viewBox="0 0 24 24" aria-hidden="true" fill="none"><path d="M5 12h14m-6-6 6 6-6 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
          </section>
          <section class="region-library-preview" aria-label="所选区域预览" :aria-busy="previewLoading">
            <div class="region-preview-surface">
              <svg v-if="preview" class="region-preview-map" :viewBox="previewBox" role="img" :aria-label="`${preview.name}本地轮廓预览`" preserveAspectRatio="xMidYMid meet">
                <path v-for="feature in previewGroups.waterArea" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="region-preview-water"/>
                <path v-for="feature in previewGroups.park" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="region-preview-park"/>
                <path v-for="feature in previewGroups.building" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="region-preview-building"/>
                <path v-for="feature in previewGroups.waterLine" :key="feature.id" :d="feature.d" class="region-preview-water-line"/>
                <path v-for="feature in previewGroups.roadMajor" :key="feature.id" :d="feature.d" class="region-preview-road-major"/>
                <path v-for="feature in previewGroups.roadLocal" :key="feature.id" :d="feature.d" class="region-preview-road-local"/>
                <path v-for="feature in previewGroups.roadPath" :key="feature.id" :d="feature.d" class="region-preview-road-path"/>
                <rect v-if="previewExtent" v-bind="previewExtent" class="region-preview-extent"/>
              </svg>
              <div v-else class="region-preview-placeholder" role="status"><span class="region-preview-rings" aria-hidden="true"></span><p>{{ previewLoading || loading ? '正在准备区域轮廓…' : selected ? '区域预览暂时不可用' : '选择区域后，在这里预览' }}</p></div>
              <span v-if="preview" class="region-preview-caption">本地轮廓预览</span>
            </div>
            <div class="region-preview-description"><h2>{{ preview?.name || '每片区域，都有自己的生活半径。' }}</h2>
              <p v-if="preview">{{ preview.nodeCount.toLocaleString() }} 个路网节点，{{ preview.edgeCount.toLocaleString() }} 条步行路段<small>范围由当前路网数据决定。</small></p>
              <p v-else>区域包包含地图轮廓与步行路网。</p>
              <a v-if="preview?.source?.provider === 'OpenStreetMap contributors'" class="region-preview-credit" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors · ODbL</a>
            </div>
          </section>
        </div>
        <footer class="region-library-footer"><span>从街道出发，理解生活的尺度。</span><span>区域包可由路网编辑器生成</span></footer>
      </main>
    </Transition>
  </div>
</template>
