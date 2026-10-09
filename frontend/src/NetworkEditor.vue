<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import { getActiveRegion } from './regionLoader.js';
import { constrainMapView, metricScale } from './mapCoverage.js';
import { createEditorBaiduMap } from './editorBaiduMap.js';
import { sampleLocalPath } from './mapAsset.js';
import { kinds, xy, distance, newId, pathLength, moveNode, splitEdge,
  projectToPath, edgeProperties, findRoute, edgesInRectangle, updateEdges, removeNode, removeEdges, edgeRemovalPlan, editShapePoint, normalizeEditorGraph } from './networkEditorModel.js';
import { GraphHistory, graphSnapshot } from './networkEditorHistory.js';
import { pathBounds, intersectsBounds, backdropBounds, NodeGrid, canvasPixelRatio } from './networkEditorGeometry.js';
import './networkEditor.css';

const emit = defineEmits(['choose-region']);
const region = getActiveRegion();
const canvas = ref(null), stage = ref(null), baiduElement = ref(null), graph = shallowRef(null);
const basemap = ref(region.authoringWorkspace ? 'baidu' : 'local'), basemapState = ref('idle'), basemapError = ref('');
const packageBounds = ref(null), packagePrompt = ref(false), packagePreview = ref(null), packageResult = ref(null);
const packageForm = ref({ id: '', name: '', version: '0.1.0' });
const contourBounds = ref(null), contourPrompt = ref(false), contourPreview = shallowRef(null);
let baiduController, baiduGeneration = 0, disposed = false;
const revision = ref(''), tick = ref(0), loading = ref(true), busy = ref(false), dirty = ref(false);
const message = ref('正在读取现有 C++ 路网…'), error = ref(''), tool = ref('select');
const selection = ref(null), search = ref(''), showContext = ref(true), showSource = ref(false), showNodes = ref(true);
const selectedEdgeIds = ref([]), multiSelectMode = ref(false), batchForm = ref({ kind: '', waitSeconds: '' });
const visible = ref(Object.fromEntries(Object.keys(kinds).map(k => [k, true])));
const connectKind = ref(region.authoringWorkspace ? 'walkway' : 'turn'), nodeForm = ref({ x: 0, y: 0 }), edgeForm = ref({});
const audit = ref(null), engine = ref(''), undoCount = ref(0), redoCount = ref(0), deletePrompt = ref(null);
const route = shallowRef(null), routeStart = ref(null), connectStart = ref(null);
const pointer = ref([0, 0]), scaleBar = ref(null), draftAvailable = ref(false);
let context = getActiveRegion().context;
const contourCount = ref(context.features.length);
const draftKey = `living-circle-network-editor-v1-${getActiveRegion().id}`;
let coverageBounds = getActiveRegion().displayBounds;
const history = new GraphHistory();
const selectionPage = ref(0), selectionPageSize = 100;
let nodes = new Map(), edges = new Map(), incidence = new Map(), drag = null, nodeGrid;
let edgeBounds = new Map(), baseLayer, baseDirty = true, paintRequested = false, pendingPointer, renderDpr = 1;
let view = { x: 0, y: 0, scale: 1 }, width = 1, height = 1, observer, frame, draftTimer;
let pendingWheelZoom = null, wheelRemainder = 0, wheelTime = 0;
let bends = [], backdrop = [];
const tools = [
  { id: 'select', label: '选择', key: 'V', icon: '↖' },
  { id: 'move', label: '移动节点', key: 'M', icon: '✥' },
  { id: 'node', label: '新增节点', key: 'N', icon: '⊕' },
  { id: 'delete-node', label: '删除节点', key: 'D', icon: '⊖' },
  { id: 'connect', label: '连线', key: 'L', icon: '⌁' },
  { id: 'split', label: '拆分路段', key: 'S', icon: '⋈' },
  { id: 'route', label: '路径检查', key: 'R', icon: '⇝' },
  { id: 'package', label: '框选打包', key: 'B', icon: '▣' },
  { id: 'context', label: '本地轮廓', key: 'C', icon: '◫' },
];
const selected = computed(() => {
  tick.value;
  return selection.value?.type === 'node' ? nodes.get(selection.value.id) : edges.get(selection.value?.id);
});
const selectedEdges = computed(() => { tick.value; return selectedEdgeIds.value.map(id => edges.get(id)).filter(Boolean); });
const selectedPageRows = computed(() => selectedEdges.value.slice(selectionPage.value * selectionPageSize, (selectionPage.value + 1) * selectionPageSize));
const selectionPages = computed(() => Math.ceil(selectedEdges.value.length / selectionPageSize));
const selectedLength = computed(() => selectedEdges.value.reduce((sum, edge) => sum + pathLength(edge.pathMeters), 0));
const showToolNodes = computed(() => ['connect', 'move', 'route', 'delete-node'].includes(tool.value) || (region.authoringWorkspace && tool.value === 'node'));
const stats = computed(() => { tick.value; return graph.value ? { nodes: graph.value.nodes.length, edges: graph.value.edges.length } : null; });
const incident = computed(() => {
  tick.value;
  return selection.value?.type === 'node' ? graph.value.edges.filter(e => e.from === selected.value?.id || e.to === selected.value?.id) : [];
});
const results = computed(() => {
  tick.value;
  const query = search.value.trim().toLowerCase();
  if (!query || !graph.value) return [];
  return [...graph.value.edges.filter(e => `${e.id} ${e.streetBlockId ?? ''} ${e.sourceWayId ?? ''}`.toLowerCase().includes(query))
    .slice(0, 12).map(e => ({ type: 'edge', id: e.id })),
    ...graph.value.nodes.filter(n => n.id.toLowerCase().includes(query)).slice(0, 8).map(n => ({ type: 'node', id: n.id }))];
});
const hint = computed(() => ({
  select: multiSelectMode.value ? '点击路段加入或移出选择，拖动框选。右键拖动平移，Esc 清空。' : '点击查看属性；Shift 点击多选，Shift 拖动框选。拖动空白平移。',
  move: '拖动节点；相连路段端点一起移动。坐标重合不会自动合并。',
  node: '点击地图新增节点，再用“连线”接入路网。',
  'delete-node': '点击节点删除；确认框会列出相连路段。右键拖动平移，Ctrl+Z 恢复。',
  connect: connectStart.value ? '起点已选。点终点完成连线，点空白添加折点；也可搜索终点 ID。' : '节点已显示。先点起点，再点终点；也可搜索节点 ID 精确选择。',
  split: '点击路段内部拆出连接节点。过街边请单独重画，避免重复等待。',
  route: routeStart.value ? '选择终点节点，检查是否连通和过街等待。' : '选择起点节点，再选择终点节点。',
  shape: '点击所选路段添加形状点。形状点只改变线形，不建立连接。',
  package: '拖动框选要打包的区域。右键拖动平移；边界通道会裁剪，过街和转弯只保留完整连接。',
  context: '拖动框选要生成本地轮廓的街区。空白路网也可生成；右键拖动平移，滚轮缩放。',
}[tool.value]));

function toScreen([x, y]) { return baiduController?.toScreen([x,y]) || [width / 2 + (x - view.x) * view.scale, height / 2 - (y - view.y) * view.scale]; }
function toWorld([x, y]) { return baiduController?.toWorld([x,y]) || [view.x + (x - width / 2) / view.scale, view.y - (y - height / 2) / view.scale]; }
function eventPoint(event) { const box = canvas.value.getBoundingClientRect(); return [event.clientX - box.left, event.clientY - box.top]; }
function queueFrame() {
  if (!frame) frame = requestAnimationFrame(() => {
    frame = null;
    if (pendingWheelZoom) {
      const { factor, point } = pendingWheelZoom; pendingWheelZoom = null;
      applyZoom(factor, point); paintRequested = true;
    }
    if (pendingPointer) { pointer.value = pendingPointer; pendingPointer = null; }
    if (paintRequested) { paintRequested = false; draw(); }
  });
}
function redraw(invalidate = false) { if (invalidate === true) baseDirty = true; paintRequested = true; queueFrame(); }
function updateHistoryCounts() { undoCount.value = history.undoCount; redoCount.value = history.redoCount; }
function rebuild() {
  nodes = new Map(graph.value.nodes.map(n => [n.id, n]));
  edges = new Map(graph.value.edges.map(e => [e.id, e]));
  incidence = new Map(graph.value.nodes.map(n => [n.id, []]));
  for (const edge of edges.values()) { incidence.get(edge.from)?.push(edge); incidence.get(edge.to)?.push(edge); }
  const bounds = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const edge of graph.value.edges) for (const [x, y] of edge.pathMeters) {
    bounds.minX = Math.min(bounds.minX, x); bounds.maxX = Math.max(bounds.maxX, x);
    bounds.minY = Math.min(bounds.minY, -y); bounds.maxY = Math.max(bounds.maxY, -y);
  }
  if (Number.isFinite(bounds.minX)) {
    coverageBounds = bounds;
  }
  nodeGrid = new NodeGrid(graph.value.nodes);
  edgeBounds = new Map(graph.value.edges.map(edge => [edge.id, pathBounds(edge.pathMeters)]));
  selectedEdgeIds.value = selectedEdgeIds.value.filter(id => edges.has(id));
  if (selection.value && !(selection.value.type === 'node' ? nodes : edges).has(selection.value.id)) selection.value = null;
  tick.value++; redraw(true);
}
function snapshot() { return graphSnapshot(graph.value); }
function record(before) {
  if (!history.record(before, snapshot())) return false;
  updateHistoryCounts();
  dirty.value = true; audit.value = null; engine.value = ''; route.value = null; error.value = '';
  rebuild(); scheduleDraft();
  return true;
}
function change(action) {
  if (busy.value || !graph.value) return false;
  const before = snapshot();
  try { action(); return record(before); }
  catch (exc) { Object.assign(graph.value, before); rebuild(); error.value = exc.message; return false; }
}
function undo(redo = false) {
  if (busy.value) return;
  if (!(redo ? history.redo(graph.value) : history.undo(graph.value))) return;
  updateHistoryCounts();
  clearSelection(); dirty.value = true; route.value = null; audit.value = null; engine.value = '';
  setTool('select'); rebuild(); scheduleDraft(); message.value = redo ? '已重做' : '已撤销';
}
function scheduleDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(writeDraft, 600);
}
function writeDraft() {
  if (!dirty.value || !graph.value) return;
  try { localStorage.setItem(draftKey, JSON.stringify({ revision: revision.value, nodes: graph.value.nodes, edges: graph.value.edges })); }
  catch { message.value = '浏览器草稿空间不足，请导出 JSON 保留修改'; }
}
async function api(method, endpoint = '', body) {
  const response = await fetch(`/api/v1/network-editor${endpoint}?regionId=${encodeURIComponent(region.id)}`, { method,
    headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail?.message ?? (typeof data.detail === 'string' ? data.detail : `请求失败 HTTP ${response.status}`));
  return data;
}
function payload() { return { revision: revision.value, nodes: graph.value.nodes, edges: graph.value.edges }; }
async function load() {
  loading.value = true; error.value = '';
  try {
    const data = await api('GET'); graph.value = normalizeEditorGraph(data.graph); revision.value = data.revision;
    const response = await fetch(region.assets.context, { cache: 'no-cache' });
    if (!response.ok) throw new Error('本地轮廓无法读取');
    context = await response.json(); contourCount.value = context.features.length; contourPreview.value = null;
    displayContext(context);
    audit.value = data.audit; history.clear(); updateHistoryCounts();
    dirty.value = false; clearSelection(); route.value = null; engine.value = ''; setTool('select');
    draftAvailable.value = Boolean(localStorage.getItem(draftKey));
    rebuild(); await nextTick(); fit(false);
    message.value = graph.value.edges.length ? `已加载 ${region.name}，修改后点击“保存到 C++ 路网”` : `已打开 ${region.name} 空白路网，请放大到需要的街区后新增节点并连线`;
  } catch (exc) { error.value = `${exc.message}。请确认本地后端已启动。`; }
  finally { loading.value = false; }
}
async function save() {
  if (busy.value) return;
  busy.value = true; error.value = ''; message.value = '正在检查并保存路网…';
  try {
    const data = await api('PUT', '', payload()); graph.value = normalizeEditorGraph(data.graph); revision.value = data.revision;
    audit.value = data.audit; engine.value = data.engine || ''; dirty.value = false; rebuild();
    discardContours();
    clearTimeout(draftTimer); localStorage.removeItem(draftKey); draftAvailable.value = false;
    message.value = `已保存 ${region.name}。备份：${data.backup}`;
  } catch (exc) { error.value = exc.message; writeDraft(); }
  finally { busy.value = false; }
}
async function validate() {
  busy.value = true; error.value = ''; message.value = '正在进行结构和 C++ 引擎检查…';
  try { const data = await api('POST', '/validate', payload()); audit.value = data.audit; engine.value = data.engine;
    message.value = data.status === 'draft' ? '草稿结构检查通过；绘制步行通道后可进行 C++ 路由检查' : `检查通过：${data.audit.componentCount} 个连通分量，C++ ${data.engine} 接受此路网`; }
  catch (exc) { error.value = exc.message; }
  finally { busy.value = false; }
}
function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey));
    if (draft.revision !== revision.value) throw new Error('草稿对应的路网已变化，请先下载草稿，人工核对后再导入');
    change(() => { graph.value.nodes = draft.nodes; graph.value.edges = normalizeEditorGraph(draft).edges; });
    draftAvailable.value = false; message.value = '已恢复本机草稿，尚未保存到 C++ 路网';
  } catch (exc) { error.value = exc.message; }
}
function download(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportGraph() { download(graph.value, `${region.id}.edited.json`); message.value = '已导出 JSON；写入当前区域包请点击保存'; }
function downloadDraft() {
  try { download(JSON.parse(localStorage.getItem(draftKey)), `${region.id}.draft.json`); }
  catch (exc) { error.value = exc.message; }
}
function reload() {
  if (!dirty.value || window.confirm('重新加载会放弃当前画布修改。已保存的本机草稿仍可恢复。')) load();
}
function chooseRegion(identity = null) {
  if (busy.value) return;
  writeDraft();
  if (dirty.value && !identity && !window.confirm('当前修改会保留为本机草稿。现在切换区域？')) return;
  emit('choose-region', identity);
}
async function changeBasemap() {
  resetWheelZoom();
  const generation = ++baiduGeneration;
  baiduController?.dispose(); baiduController = null;
  basemapError.value = ''; basemapState.value = 'idle'; redraw(true);
  if (basemap.value !== 'baidu') return;
  const ak = import.meta.env.VITE_BAIDU_BROWSER_AK?.trim();
  if (!ak) { basemapError.value = '未配置浏览器地图 AK，请在 frontend/.env.local 配置后重启前端。当前继续使用本地底图。'; basemap.value = 'local'; return; }
  basemapState.value = 'loading';
  try {
    const controller = await createEditorBaiduMap(baiduElement.value, region, ak, () => redraw(true));
    if (disposed || generation !== baiduGeneration) { controller.dispose(); return; }
    baiduController = controller;
    view = controller.sync(view, width, height, region.authoringWorkspace ? null : coverageBounds);
    basemapState.value = 'ready'; redraw(true);
  } catch {
    if (disposed || generation !== baiduGeneration) return;
    baiduController?.dispose(); baiduController = null;
    basemapState.value = 'error'; basemap.value = 'local';
    basemapError.value = '百度底图加载失败，请检查网络、浏览器 AK 和域名白名单。当前继续使用本地底图。'; redraw(true);
  }
}
function displayContext(value) {
  backdrop = value.features.map(f => ({ kind: f.kind, path: new Path2D(f.d),
    fillRule: f.fillRule || 'nonzero', bounds: backdropBounds(f.d) }));
  redraw(true);
}
function discardContours() { contourPreview.value = null; displayContext(context); }
function selectContours() { if (!busy.value) setTool('context'); }
async function generateContours() {
  if (!contourBounds.value || busy.value) return;
  busy.value = true; error.value = ''; message.value = '正在下载 OSM 地物并校准坐标…';
  try {
    const preview = await api('POST', '/context/preview', { revision: revision.value, boundsMeters: contourBounds.value });
    contourPreview.value = preview; displayContext(preview.context);
    showContext.value = true; showSource.value = true;
    basemap.value = 'local'; await changeBasemap();
    contourPrompt.value = false; setTool('select');
    message.value = `已生成 ${preview.featureCount} 个本地轮廓，正在预览；在右侧保存到区域包或取消预览`;
  } catch (exc) { error.value = exc.message; message.value = '本地轮廓生成失败，可调整选框后重试'; }
  finally { busy.value = false; }
}
async function saveContours() {
  if (!contourPreview.value || busy.value) return;
  busy.value = true; error.value = ''; message.value = '正在保存本地轮廓…';
  try {
    const data = await api('PUT', '/context', { revision: revision.value, previewId: contourPreview.value.previewId });
    revision.value = data.revision; context = data.context; contourCount.value = data.featureCount;
    discardContours(); writeDraft();
    message.value = `已保存 ${data.featureCount} 个本地轮廓。备份：${data.backup}`;
  } catch (exc) { error.value = exc.message; }
  finally { busy.value = false; }
}
async function previewPackage() {
  if (!packageBounds.value || busy.value) return;
  busy.value = true; error.value = ''; packagePreview.value = null; packageResult.value = null; packagePrompt.value = true;
  packageForm.value = { id: `${region.id.slice(0,40)}-area-${Date.now().toString(36)}`, name: `${region.name}子区域`, version: '0.1.0' };
  try { packagePreview.value = await api('POST', '/package/preview', { ...payload(), boundsMeters: packageBounds.value }); }
  catch (exc) { error.value = exc.message; packagePrompt.value = false; }
  finally { busy.value = false; }
}
async function buildPackage() {
  if (busy.value || !packagePreview.value) return;
  busy.value = true; error.value = '';
  try {
    packageResult.value = await api('POST', '/package', { ...payload(), boundsMeters: packageBounds.value, ...packageForm.value });
    message.value = `已生成 ${packageResult.value.name}，可下载 ZIP 或打开继续编辑`;
    const link = document.createElement('a'); link.href = packageResult.value.downloadUrl; link.download = packageResult.value.archiveName; link.click();
  } catch (exc) { error.value = exc.message; }
  finally { busy.value = false; }
}
async function importGraph(event) {
  const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
  busy.value = true;
  try {
    const incoming = normalizeEditorGraph(JSON.parse(await file.text()));
    if (incoming.schemaVersion !== 2 || incoming.synthetic !== true || !Array.isArray(incoming.nodes) || !Array.isArray(incoming.edges)) {
      throw new Error('请选择本编辑器导出的 v2 合成路网 JSON');
    }
    const originKey = graph.value.originWgs84 ? 'originWgs84' : 'originBd09';
    if (JSON.stringify(incoming[originKey]) !== JSON.stringify(graph.value[originKey])) throw new Error('导入文件的坐标原点不一致');
    await api('POST', '/validate', { revision: revision.value, nodes: incoming.nodes, edges: incoming.edges });
    busy.value = false;
    change(() => { graph.value.nodes = incoming.nodes; graph.value.edges = incoming.edges; });
    message.value = '导入并检查通过，尚未保存到 C++ 路网'; fit(true);
  } catch (exc) { error.value = exc.message; }
  finally { busy.value = false; }
}
function fit(all) {
  resetWheelZoom();
  if (!graph.value) return;
  const points = graph.value.nodes.map(xy);
  if (!points?.length) {
    view = { x: 0, y: 0, scale: Math.max(0.01, Math.min(width,height)/12000) };
    redraw(true); return;
  }
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  view = { x: minX / 2 + maxX / 2, y: minY / 2 + maxY / 2,
    scale: Math.max(0.03, Math.min(40, width / (maxX - minX + 180), height / (maxY - minY + 180))) };
  redraw(true);
}
function zoom(factor, point = [width / 2, height / 2]) {
  resetWheelZoom(); applyZoom(factor, point); redraw(true);
}
function applyZoom(factor, point) {
  if (!Number.isFinite(factor) || factor <= 0 || factor === 1) return;
  if (baiduController) {
    const adjusted = baiduController.zoom(factor, point, width, height);
    if (adjusted) view = adjusted;
  } else {
    const before = toWorld(point); view.scale = Math.max(0.03, Math.min(40, view.scale * factor));
    const after = toWorld(point); view.x += before[0] - after[0]; view.y += before[1] - after[1];
  }
  baseDirty = true;
}
function resetWheelZoom() { pendingWheelZoom = null; wheelRemainder = 0; wheelTime = 0; }
function wheelZoom(event) {
  if (!graph.value || loading.value || disposed) return;
  // Wheel events can be pixels, text lines or pages; native Baidu zoom uses whole levels.
  const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? height : 1;
  const delta = Math.max(-300, Math.min(300, event.deltaY * unit));
  if (!Number.isFinite(delta) || delta === 0) return;
  let factor;
  if (baiduController) {
    if (event.timeStamp - wheelTime > 250 || Math.sign(delta) !== Math.sign(wheelRemainder)) wheelRemainder = 0;
    wheelTime = event.timeStamp; wheelRemainder += delta;
    const levels = -Math.trunc(wheelRemainder / 100);
    if (!levels) return;
    wheelRemainder += levels * 100;
    const pendingLevels = Math.log2(pendingWheelZoom?.factor || 1) + levels;
    factor = 2 ** Math.max(-3, Math.min(3, pendingLevels));
  } else factor = (pendingWheelZoom?.factor || 1) * Math.exp(-delta * 0.0015);
  pendingWheelZoom = { factor, point: eventPoint(event) }; queueFrame();
}
function setTool(id) {
  if (id === 'context') { discardContours(); contourPrompt.value = false; }
  tool.value = id; if (id !== 'select') multiSelectMode.value = false;
  connectStart.value = null; routeStart.value = null; bends = [];
  if (id === 'connect') clearSelection();
  redraw(true);
}
function cancelConnection() { connectStart.value = null; bends = []; clearSelection(); redraw(); }
function clearSelection() { selection.value = null; selectedEdgeIds.value = []; redraw(); }
function selectEdges(ids) {
  selectedEdgeIds.value = [...new Set(ids)].filter(id => edges.has(id));
  selection.value = selectedEdgeIds.value.length ? { type: 'edge', id: selectedEdgeIds.value.at(-1) } : null;
  redraw();
}
function toggleMultiSelect() { setTool('select'); multiSelectMode.value = !multiSelectMode.value; if (selection.value?.type === 'node') clearSelection(); }
function choose(type, id, locate = false, additive = false) {
  if (type === 'edge') selectEdges(additive ? (selectedEdgeIds.value.includes(id)
    ? selectedEdgeIds.value.filter(value => value !== id) : [...selectedEdgeIds.value, id]) : [id]);
  else { selectedEdgeIds.value = []; selection.value = { type, id }; }
  if (locate) {
    const item = type === 'node' ? nodes.get(id) : edges.get(id);
    if (!item) return;
    const point = type === 'node' ? xy(item) : item.pathMeters[Math.floor(item.pathMeters.length / 2)];
    view.x = point[0]; view.y = point[1]; view.scale = Math.max(view.scale, 2); redraw(true);
  }
}
function chooseSearchResult(item, event) {
  choose(item.type, item.id, true, event.shiftKey || multiSelectMode.value);
  search.value = '';
  if (tool.value === 'connect' && item.type === 'node') connectNode(item.id);
}
function chooseNode(id, locate = false) {
  choose('node', id, locate);
  if (tool.value === 'connect') connectNode(id);
}
function startFromNode(id) { setTool('connect'); connectNode(id); }
function connectNode(id) {
  if (busy.value) return;
  const node = nodes.get(id);
  if (!node) { error.value = '节点不存在，请重新选择'; return; }
  error.value = '';
  if (!connectStart.value) {
    connectStart.value = id; bends = []; choose('node', id);
    message.value = `已选择起点 ${id}，请点击或搜索终点节点`; redraw(); return;
  }
  if (id === connectStart.value) { error.value = '起点和终点不能是同一节点'; return; }
  const start = nodes.get(connectStart.value);
  if (!start) { cancelConnection(); error.value = '起点已失效，请重新选择'; return; }
  const edgeId = newId('edge');
  const edge = { id: edgeId, from: start.id, to: id, pathMeters: [xy(start), ...bends, xy(node)] };
  const changed = change(() => {
    if (pathLength(edge.pathMeters) < 0.05) throw new Error('这两个节点的连线不足 0.05 米，请调整位置或折点');
    graph.value.edges.push(edgeProperties(edge, { kind: connectKind.value, widthMeters: 4, waitSeconds: 20 }));
  });
  if (changed) {
    connectStart.value = null; bends = []; choose('edge', edgeId);
    message.value = `已新增${kinds[connectKind.value].label}，可在右侧修改属性或继续选择下一条连线的起点`;
  }
  redraw();
}
watch([selection, tick], () => {
  redraw();
  if (!selected.value) return;
  if (selection.value.type === 'node') nodeForm.value = { x: selected.value.xMeters, y: selected.value.yMeters };
  else edgeForm.value = { ...selected.value, accessMode: selected.value.accessMode ?? 'shared', waitSeconds: selected.value.waitSeconds ?? 20,
    widthMeters: selected.value.widthMeters ?? 4, side: selected.value.side || 'left' };
  redraw();
});
watch(selectedEdgeIds, () => { batchForm.value = { kind: '', waitSeconds: '' }; selectionPage.value = 0; redraw(); });
watch(connectKind, () => redraw());
watch([visible, showContext, showSource, showNodes], () => redraw(true), { deep: true });
function hitNode(point) {
  let found = null, best = 9;
  for (const id of nodeGrid?.near(toWorld(point), 9 / view.scale) ?? []) {
    const node = nodes.get(id); if (!node) continue;
    const linked = incidence.get(node.id) ?? [];
    if (tool.value !== 'connect' && linked.length && !linked.some(e => visible.value[e.kind])) continue;
    const d = distance(point, toScreen(xy(node))); if (d < best) { found = node; best = d; }
  }
  return found;
}
function hitEdge(point) {
  const world = toWorld(point); let found = null, best = 8 / view.scale;
  const box = { minX: world[0] - best, maxX: world[0] + best, minY: world[1] - best, maxY: world[1] + best };
  for (const edge of edges.values()) {
    if (!visible.value[edge.kind] || !intersectsBounds(edgeBounds.get(edge.id), box)) continue;
    const p = projectToPath(world, edge.pathMeters);
    if (p.distance < best) { best = p.distance; found = edge; }
  }
  return found;
}
function press(event) {
  if (!graph.value || busy.value || event.button > 2) return;
  error.value = ''; canvas.value.focus();
  const screen = eventPoint(event), world = toWorld(screen), node = hitNode(screen);
  if (event.button === 1 || event.button === 2 || event.altKey) {
    canvas.value.setPointerCapture(event.pointerId);
    drag = { type: 'pan', screen, view: { ...view } }; return;
  }
  if (tool.value === 'package' || tool.value === 'context') {
    canvas.value.setPointerCapture(event.pointerId);
    drag = { type: tool.value, screen, end: screen, startWorld: world, moved: false }; return;
  }
  if (tool.value === 'select' && (event.shiftKey || multiSelectMode.value)) {
    canvas.value.setPointerCapture(event.pointerId);
    drag = { type: 'multi-select', screen, end: screen, edgeId: hitEdge(screen)?.id,
      previous: [...selectedEdgeIds.value], moved: false }; return;
  }
  if (tool.value === 'node') {
    const id = newId('node');
    change(() => graph.value.nodes.push({ id, xMeters: world[0], yMeters: world[1], verificationStatus: 'user_edited_unverified' }));
    choose('node', id); return;
  }
  if (tool.value === 'delete-node') {
    if (!node) { error.value = '请点击节点；节点密集时先放大地图'; return; }
    choose('node', node.id); askDelete(); redraw(); return;
  }
  if (tool.value === 'connect') {
    if (!node) { if (connectStart.value) { bends.push(world); redraw(); } else error.value = '请先选择起点节点'; return; }
    connectNode(node.id); return;
  }
  if (tool.value === 'route') {
    if (!node) { error.value = '请放大后点击节点'; return; }
    choose('node', node.id);
    if (!routeStart.value) { routeStart.value = node.id; route.value = null; }
    else {
      route.value = findRoute(graph.value, routeStart.value, node.id);
      message.value = route.value ? `路径 ${route.value.edges.length} 段，${route.value.seconds.toFixed(1)} 秒，过街等待 ${route.value.waitSeconds} 秒` : '这两个节点在当前路网中不连通';
      routeStart.value = null;
    }
    redraw(); return;
  }
  if (tool.value === 'shape' && selection.value?.type === 'edge') {
    const edge = selected.value, p = projectToPath(world, edge.pathMeters);
    if (p.distance * view.scale > 10) { error.value = '请点击选中的路段'; return; }
    change(() => editShapePoint(graph.value, edge.id, p.index, p.point, true)); setTool('select'); return;
  }
  // Shape handles are independent of topology nodes.
  if (tool.value === 'select' && selectedEdgeIds.value.length === 1 && selection.value?.type === 'edge') {
    const edge = selected.value;
    const index = edge?.pathMeters.findIndex((p, i) => i > 0 && i < edge.pathMeters.length - 1 && distance(screen, toScreen(p)) < 8);
    if (index > 0) { canvas.value.setPointerCapture(event.pointerId); drag = { type: 'shape', id: edge.id, index, before: snapshot(), screen, moved: false }; return; }
  }
  if (tool.value === 'move' && node) {
    canvas.value.setPointerCapture(event.pointerId);
    choose('node', node.id); drag = { type: 'node', id: node.id, before: snapshot(), screen, moved: false }; return;
  }
  const edge = hitEdge(screen);
  if (tool.value === 'split') {
    if (!edge) { error.value = '请点击路段内部'; return; }
    let id; change(() => { id = splitEdge(graph.value, edge.id, world); });
    if (id) choose('node', id); return;
  }
  if (node && (view.scale >= 0.7 || !edge)) choose('node', node.id);
  else if (edge) choose('edge', edge.id);
  else { clearSelection(); canvas.value.setPointerCapture(event.pointerId); drag = { type: 'pan', screen, view: { ...view } }; }
  redraw();
}
function move(event) {
  const screen = eventPoint(event); pendingPointer = toWorld(screen); queueFrame();
  if (['multi-select', 'package', 'context'].includes(drag?.type)) {
    drag.end = screen; if (distance(screen, drag.screen) > 4) drag.moved = true;
  } else if (drag?.type === 'pan') {
    view.x = drag.view.x - (screen[0] - drag.screen[0]) / view.scale;
    view.y = drag.view.y + (screen[1] - drag.screen[1]) / view.scale;
    pendingPointer = toWorld(screen);
    baseDirty = true;
  } else if (drag?.type === 'node' || drag?.type === 'shape') {
    if (distance(screen, drag.screen) > 2) drag.moved = true;
    if (drag.moved) {
      if (drag.type === 'node') {
        moveNode(graph.value, drag.id, toWorld(screen));
        nodes.set(drag.id, graph.value.nodes.find(node => node.id === drag.id));
        for (const edge of graph.value.edges) if (edge.from === drag.id || edge.to === drag.id) {
          edges.set(edge.id, edge); edgeBounds.set(edge.id, pathBounds(edge.pathMeters));
        }
      } else {
        editShapePoint(graph.value, drag.id, drag.index, toWorld(screen));
        const edge = graph.value.edges.find(item => item.id === drag.id);
        edges.set(drag.id, edge); edgeBounds.set(drag.id, pathBounds(edge.pathMeters));
      }
      baseDirty = true;
    }
  }
  if (drag || showToolNodes.value || connectStart.value) redraw();
}
function release(event) {
  if (drag?.type === 'context') {
    const end = toWorld(eventPoint(event)), begin = drag.startWorld;
    if (drag.moved) {
      contourBounds.value = { minX: Math.min(begin[0],end[0]), maxX: Math.max(begin[0],end[0]),
        minY: Math.min(begin[1],end[1]), maxY: Math.max(begin[1],end[1]) };
      contourPrompt.value = true;
    }
  } else if (drag?.type === 'package') {
    const end = toWorld(eventPoint(event)), begin = drag.startWorld;
    if (drag.moved) {
      packageBounds.value = { minX: Math.max(coverageBounds.minX, Math.min(begin[0],end[0])),
        maxX: Math.min(coverageBounds.maxX, Math.max(begin[0],end[0])),
        minY: Math.max(-coverageBounds.maxY, Math.min(begin[1],end[1])),
        maxY: Math.min(-coverageBounds.minY, Math.max(begin[1],end[1])) };
      previewPackage();
    }
  } else if (drag?.type === 'multi-select') {
    const end = eventPoint(event);
    if (drag.moved) selectEdges([...drag.previous, ...edgesInRectangle(graph.value.edges,
      toWorld(drag.screen), toWorld(end), visible.value)]);
    else if (drag.edgeId) choose('edge', drag.edgeId, false, true);
  } else if (drag?.moved) record(drag.before);
  drag = null; redraw();
}
function cancel() {
  if (drag?.before) { Object.assign(graph.value, drag.before); rebuild(); }
  drag = null; setTool('select'); deletePrompt.value = null; clearSelection();
}
function applyNode() {
  const point = [Number(nodeForm.value.x), Number(nodeForm.value.y)];
  change(() => moveNode(graph.value, selected.value.id, point));
}
function applyEdge() {
  const id = selected.value.id;
  change(() => { const index = graph.value.edges.findIndex(e => e.id === id);
    graph.value.edges[index] = edgeProperties(selected.value, edgeForm.value); });
}
function applyBatch() {
  let count;
  change(() => { count = updateEdges(graph.value, selectedEdgeIds.value, batchForm.value); });
  if (count) message.value = `已批量修改 ${count} 条路段，可用撤销恢复`;
}
function askDelete() {
  if (!selected.value || busy.value) return;
  const ids = selection.value.type === 'edge' ? [...selectedEdgeIds.value] : incident.value.map(edge => edge.id);
  const plan = edgeRemovalPlan(graph.value, ids);
  deletePrompt.value = selection.value.type === 'edge'
    ? { type: 'edges', ids, count: ids.length, nodeIds: plan.nodeIds }
    : { ...selection.value, count: ids.length, edgeIds: ids, nodeIds: plan.nodeIds.filter(id => id !== selection.value.id) };
}
function removeSelected() {
  const target = deletePrompt.value; if (!target) return;
  let removed;
  const changed = change(() => { removed = target.type === 'edges' ? removeEdges(graph.value, target.ids) : removeNode(graph.value, target.id); });
  deletePrompt.value = null;
  if (!changed) return;
  clearSelection();
  if (tool.value !== 'delete-node') setTool('select');
  message.value = `已删除 ${removed.edgeIds.length} 条路段和 ${removed.nodeIds.length} 个节点，可用 Ctrl+Z 恢复`;
}
function keydown(event) {
  if (contourPrompt.value) { if (event.key === 'Escape' && !busy.value) contourPrompt.value = false; return; }
  if (packagePrompt.value) { if (event.key === 'Escape' && !busy.value) packagePrompt.value = false; return; }
  if (event.key === 'Escape') { cancel(); return; }
  if (deletePrompt.value) return;
  if (/INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || busy.value) return;
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && key === 's') { event.preventDefault(); if (dirty.value) save(); }
  else if ((event.ctrlKey || event.metaKey) && key === 'z') { event.preventDefault(); undo(event.shiftKey); }
  else if ((event.ctrlKey || event.metaKey) && key === 'y') { event.preventDefault(); undo(true); }
  else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); askDelete(); }
  else if (!event.ctrlKey && !event.metaKey && !event.altKey) { const option = tools.find(t => t.key.toLowerCase() === key); if (option) setTool(option.id); }
}
function beforeUnload(event) { if (dirty.value) { writeDraft(); event.preventDefault(); event.returnValue = ''; } }
function stroke(ctx, points, color, lineWidth, dashed = false) {
  const sampled = baiduController ? sampleLocalPath(points, 100) : points;
  ctx.beginPath(); sampled.forEach((p, i) => { const [x, y] = toScreen(p); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = color; ctx.lineWidth = lineWidth; ctx.setLineDash(dashed ? [5, 4] : []); ctx.stroke(); ctx.setLineDash([]);
}
function dot(ctx, point, color, radius = 3) {
  const [x, y] = toScreen(point); ctx.beginPath(); ctx.arc(x, y, radius, 0, 2 * Math.PI);
  ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = color === '#fff' ? '#be6580' : '#fff'; ctx.lineWidth = 1; ctx.stroke();
}
function drawBase() {
  const ctx = baseLayer.getContext('2d');
  ctx.setTransform(renderDpr, 0, 0, renderDpr, 0, 0); ctx.clearRect(0, 0, width, height);
  if (!baiduController) { ctx.fillStyle = '#f0f4f5'; ctx.fillRect(0, 0, width, height); }
  const grid = 10 ** Math.ceil(Math.log10(65 / view.scale));
  const northwest = toWorld([0, 0]), southeast = toWorld([width, height]);
  const viewport = { minX: northwest[0], maxX: southeast[0], minY: southeast[1], maxY: northwest[1] };
  ctx.strokeStyle = '#dde5e9'; ctx.lineWidth = 0.5; ctx.beginPath();
  const gridX = Math.ceil(northwest[0] / grid) * grid, gridY = Math.ceil(southeast[1] / grid) * grid;
  for (let i = 0, count = Math.min(256, Math.floor((southeast[0] - gridX) / grid) + 1); i < count; i++) {
    const x = gridX + i * grid;
    const screen = toScreen([x, 0])[0]; ctx.moveTo(screen, 0); ctx.lineTo(screen, height);
  }
  for (let i = 0, count = Math.min(256, Math.floor((northwest[1] - gridY) / grid) + 1); i < count; i++) {
    const y = gridY + i * grid;
    const screen = toScreen([0, y])[1]; ctx.moveTo(0, screen); ctx.lineTo(width, screen);
  }
  if (!baiduController) ctx.stroke();
  if (!baiduController && (showContext.value || showSource.value)) {
    ctx.save(); ctx.translate(width / 2 - view.x * view.scale, height / 2 + view.y * view.scale);
    ctx.scale(view.scale, view.scale); // Source SVG paths use south-positive Y.
    for (const feature of backdrop) {
      const road = feature.kind.startsWith('road');
      if ((road && !showSource.value) || (!road && !showContext.value)) continue;
      if (!intersectsBounds(feature.bounds, viewport)) continue;
      ctx.fillStyle = { park: '#dae5d9', waterArea: '#cedfe9', building: '#e0e5e8' }[feature.kind] || 'transparent';
      ctx.strokeStyle = feature.kind === 'waterLine' ? '#c1d7e4' : road ? '#c6ced3' : '#d3dade';
      ctx.lineWidth = (road ? 1.2 : 0.5) / view.scale;
      if (!road && feature.kind !== 'waterLine') ctx.fill(feature.path, feature.fillRule);
      ctx.stroke(feature.path);
    }
    ctx.restore();
  }
  if (!graph.value) return;
  const drawn = [];
  for (const edge of edges.values()) {
    if (!visible.value[edge.kind]) continue;
    if (!intersectsBounds(edgeBounds.get(edge.id), viewport)) continue;
    stroke(ctx, edge.pathMeters, kinds[edge.kind]?.color ?? '#777', edge.kind === 'crossing' ? 2.4 : 1.8, edge.kind === 'crossing'); drawn.push(edge);
  }
  const visibleNodes = new Set(drawn.flatMap(e => [e.from, e.to]));
  if ((showNodes.value && view.scale >= 0.7) || showToolNodes.value) {
    for (const node of nodes.values()) {
      if (tool.value !== 'connect' && !visibleNodes.has(node.id) && incidence.get(node.id)?.length) continue;
      const point = toScreen(xy(node));
      if (point[0] >= 0 && point[0] <= width && point[1] >= 0 && point[1] <= height) dot(ctx, xy(node), '#758d9b', tool.value === 'connect' ? 3.5 : 2.7);
    }
  }
}
function draw() {
  if (!canvas.value || !baseLayer) return;
  const requested = { scale: view.scale, translateX: width / 2 - view.x * view.scale, translateY: height / 2 + view.y * view.scale };
  const limited = region.authoringWorkspace ? requested : constrainMapView(requested, width, height, coverageBounds);
  const centerX = (width / 2 - limited.translateX) / limited.scale;
  const centerY = -(height / 2 - limited.translateY) / limited.scale;
  if (Math.abs(view.x - centerX) > 1e-6 || Math.abs(view.y - centerY) > 1e-6 || Math.abs(view.scale - limited.scale) > 1e-9) {
    view = { x: centerX, y: centerY, scale: limited.scale }; baseDirty = true;
  }
  if (baiduController) {
    try { view = baiduController.sync(view, width, height, region.authoringWorkspace ? null : coverageBounds); }
    catch (exc) {
      baiduController.dispose(); baiduController = null; basemap.value = 'local'; basemapState.value = 'error';
      basemapError.value = `${exc.message}。已恢复本地底图。`; baseDirty = true;
    }
  }
  if (baseDirty) { drawBase(); baseDirty = false; }
  const ctx = canvas.value.getContext('2d');
  ctx.setTransform(renderDpr, 0, 0, renderDpr, 0, 0); ctx.clearRect(0, 0, width, height);
  ctx.drawImage(baseLayer, 0, 0, width, height);
  if (!graph.value) return;
  if (route.value) for (const edge of route.value.edges) { stroke(ctx, edge.pathMeters, '#fff', 7); stroke(ctx, edge.pathMeters, '#cc3977', 4); }
  for (const id of selectedEdgeIds.value) {
    const edge = edges.get(id); if (!edge) continue;
    if (!visible.value[edge.kind]) continue;
    stroke(ctx, edge.pathMeters, '#fff', 7); stroke(ctx, edge.pathMeters, '#e54474', 3.5);
  }
  const active = selection.value?.type === 'node' ? nodes.get(selection.value.id) : edges.get(selection.value?.id);
  if (active) {
    if (selection.value.type === 'node') dot(ctx, xy(active), '#e54474', 6);
    else if (selectedEdgeIds.value.length === 1) {
      active.pathMeters.forEach((p, i) => dot(ctx, p, i === 0 || i === active.pathMeters.length - 1 ? '#e54474' : '#fff', 4));
    }
  }
  if (showToolNodes.value && !deletePrompt.value) {
    const hovered = hitNode(toScreen(pointer.value));
    if (hovered) dot(ctx, xy(hovered), '#e54474', 6);
  }
  if (connectStart.value && nodes.has(connectStart.value)) {
    const start = xy(nodes.get(connectStart.value));
    stroke(ctx, [start, ...bends, pointer.value], kinds[connectKind.value].color, 2.5, true); dot(ctx, start, '#e54474', 6);
    bends.forEach(p => dot(ctx, p, kinds[connectKind.value].color, 3));
  }
  if (routeStart.value) dot(ctx, xy(nodes.get(routeStart.value)), '#cc3977', 7);
  if (packageBounds.value) {
    const b = packageBounds.value;
    stroke(ctx, [[b.minX,b.minY],[b.maxX,b.minY],[b.maxX,b.maxY],[b.minX,b.maxY],[b.minX,b.minY]], '#b97815', 2, true);
  }
  if (contourBounds.value && (tool.value === 'context' || contourPreview.value)) {
    const b = contourBounds.value;
    stroke(ctx, [[b.minX,b.minY],[b.maxX,b.minY],[b.maxX,b.maxY],[b.minX,b.maxY],[b.minX,b.minY]], '#246e87', 2, true);
  }
  if (['multi-select', 'package', 'context'].includes(drag?.type) && drag.moved) {
    const x = Math.min(drag.screen[0], drag.end[0]), y = Math.min(drag.screen[1], drag.end[1]);
    const w = Math.abs(drag.end[0] - drag.screen[0]), h = Math.abs(drag.end[1] - drag.screen[1]);
    ctx.fillStyle = '#e5447418'; ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#d54070'; ctx.lineWidth = 1; ctx.setLineDash([5, 3]); ctx.strokeRect(x, y, w, h); ctx.setLineDash([]);
  }
  const scaleStart = toWorld([20, height - 55]), scaleEnd = toWorld([120, height - 55]);
  scaleBar.value = metricScale(scaleStart && scaleEnd ? distance(scaleStart, scaleEnd) / 100 : 1 / view.scale, 100);
}
onMounted(async () => {
  document.title = '路网手动编辑器 · 步行生活圈';
  baseLayer = document.createElement('canvas');
  displayContext(context);
  const resize = box => {
    width = box.width; height = box.height;
    renderDpr = canvasPixelRatio(width, height, window.devicePixelRatio || 1);
    canvas.value.width = baseLayer.width = Math.max(1, Math.floor(width * renderDpr));
    canvas.value.height = baseLayer.height = Math.max(1, Math.floor(height * renderDpr)); redraw(true);
  };
  resize(stage.value.getBoundingClientRect());
  observer = new ResizeObserver(entries => resize(entries[0].contentRect)); observer.observe(stage.value);
  window.addEventListener('keydown', keydown); window.addEventListener('beforeunload', beforeUnload);
  await load();
  if (region.authoringWorkspace) await changeBasemap();
});
onUnmounted(() => {
  resetWheelZoom();
  disposed = true; baiduGeneration++; baiduController?.dispose(); baiduController = null;
  observer?.disconnect(); clearTimeout(draftTimer); cancelAnimationFrame(frame); writeDraft();
  history.clear(); backdrop = []; if (baseLayer) { baseLayer.width = baseLayer.height = 1; baseLayer = null; }
  window.removeEventListener('keydown', keydown); window.removeEventListener('beforeunload', beforeUnload);
});
</script>

<template>
  <main class="ne-app">
    <header class="ne-header">
      <button class="ne-back" type="button" :disabled="busy" title="返回区域包选择" aria-label="返回区域包选择" @click="chooseRegion()">‹</button>
      <div class="ne-title"><h1>路网手动编辑器</h1><span>{{ region.name }} / v{{ region.version }}</span></div>
      <span class="ne-save-state" :class="{ dirty }">{{ dirty ? '有未保存修改' : '文件已加载' }}</span>
      <div class="ne-file-actions">
        <button :disabled="busy" @click="chooseRegion()">切换区域</button>
        <label class="ne-button" :class="{ disabled: loading || busy }">导入 JSON<input type="file" accept=".json,application/json" :disabled="loading || busy" @change="importGraph" /></label>
        <button :disabled="!graph || busy" @click="exportGraph">导出 JSON</button>
        <button :disabled="!graph || busy" @click="validate">{{ busy ? '处理中…' : '检查路网' }}</button>
        <button class="ne-primary" :disabled="!dirty || busy" @click="save">保存到 C++ 路网</button>
      </div>
    </header>
    <div class="ne-toolbar">
      <div class="ne-tools" role="group" aria-label="编辑工具">
        <button v-for="item in tools" :key="item.id" :class="{ active: tool === item.id }" :disabled="!graph || busy || (item.id === 'package' && !stats?.edges)"
          :aria-pressed="tool === item.id" :title="`${item.label} (${item.key})`" @click="setTool(item.id)"><b aria-hidden="true">{{ item.icon }}</b>{{ item.label }}<kbd>{{ item.key }}</kbd></button>
        <button class="ne-multi-toggle" :class="{ active: multiSelectMode }" :disabled="!graph || busy" :aria-pressed="multiSelectMode" @click="toggleMultiSelect">多选路段</button>
      </div>
      <div class="ne-history"><button :disabled="!undoCount || busy" title="撤销 Ctrl+Z" @click="undo()">↶ 撤销</button><button :disabled="!redoCount || busy" title="重做 Ctrl+Y" @click="undo(true)">↷ 重做</button></div>
    </div>
    <div v-if="tool === 'connect'" class="ne-connect-options">
      <span class="ne-connect-label">新连线类型</span>
      <div class="ne-connect-kinds" role="group" aria-label="新连线类型">
        <button v-for="(value, key) in kinds" :key="key" :class="{ active: connectKind === key }" :aria-pressed="connectKind === key" :disabled="busy" @click="connectKind = key"><i :style="{ background: value.color }"></i>{{ value.label }}</button>
      </div>
      <span v-if="connectKind === 'crossing'" class="ne-connect-wait">默认等待 20 秒</span>
      <div class="ne-connect-status"><span>{{ connectStart ? '已选起点' : '请选择起点节点' }}</span><code v-if="connectStart" :title="connectStart">{{ connectStart }}</code><button v-if="connectStart" :disabled="busy" @click="cancelConnection">取消当前连线</button></div>
    </div>
    <div v-if="draftAvailable" class="ne-draft">发现上次未保存的本机草稿。<button @click="restoreDraft">恢复草稿</button><button @click="downloadDraft">下载草稿</button><button @click="draftAvailable = false">稍后处理</button></div>
    <div v-if="error" class="ne-error" role="alert">{{ error }}<button @click="error = ''" aria-label="关闭错误">×</button></div>
    <div class="ne-workspace">
      <section ref="stage" class="ne-map" :class="{ 'with-baidu': basemapState === 'ready' }" aria-label="路网编辑画布">
        <div ref="baiduElement" class="ne-baidu" :class="{ ready: basemapState === 'ready' }" aria-hidden="true"></div>
        <canvas ref="canvas" tabindex="0" :class="`tool-${tool}`" aria-label="路网地图，使用工具栏选择编辑操作"
          @pointerdown="press" @pointermove="move" @pointerup="release" @pointercancel="cancel" @contextmenu.prevent
          @wheel.prevent="wheelZoom" />
        <div class="ne-map-help"><span class="ne-tool-dot"></span>{{ hint }}</div>
        <div v-if="selectedEdges.length" class="ne-selection-bar"><span>已选 {{ selectedEdges.length }} 条路段</span><button :disabled="busy" @click="clearSelection">清空选择</button></div>
        <div v-if="loading || !graph" class="ne-loading"><strong>{{ loading ? '正在加载路网' : '路网尚未加载' }}</strong><span>需要本机 API 服务，使用项目启动脚本启动。</span><button v-if="!loading" @click="load">重新加载</button></div>
        <div class="ne-map-controls"><button @click="zoom(1.5)" aria-label="放大地图">+</button><button @click="zoom(1 / 1.5)" aria-label="缩小地图">−</button><button @click="fit(false)">样区</button><button @click="fit(true)">全图</button></div>
        <div v-if="scaleBar" class="ne-scale" :aria-label="`地图比例尺 ${scaleBar.label}`"><span :style="{ width: `${scaleBar.pixels}px` }"></span>{{ scaleBar.label }}</div><div class="ne-north">N <span>↑</span></div>
        <div v-if="basemapState !== 'ready'" class="ne-attribution">{{ contourCount || contourPreview ? '本地轮廓 © OpenStreetMap contributors · ODbL' : region.geographicCoordType === 'bd09ll' ? '本地米制编辑画布' : '底图 © OpenStreetMap contributors · ODbL' }}</div>
      </section>
      <aside class="ne-inspector">
        <section class="ne-context-panel ne-package-panel"><h2>本地轮廓</h2><p>框选街区，生成建筑、水体、绿地和道路示意。当前已保存 {{ contourCount.toLocaleString() }} 个轮廓。</p>
          <p v-if="contourBounds" class="ne-metric">选框 {{ Math.round(contourBounds.maxX-contourBounds.minX) }} × {{ Math.round(contourBounds.maxY-contourBounds.minY) }} 米</p>
          <div><button :disabled="busy || !graph" @click="selectContours">{{ contourBounds ? '重新框选轮廓' : '生成本地轮廓' }}</button><button v-if="contourBounds && !contourPreview" :disabled="busy" @click="contourPrompt = true">生成选区轮廓</button></div>
          <template v-if="contourPreview"><p class="ne-contour-preview" role="status">正在预览 {{ contourPreview.featureCount }} 个轮廓，尚未保存。</p><p>保存后用本次轮廓替换该区域的本地底图。</p><div><button class="ne-primary" :disabled="busy" @click="saveContours">保存轮廓到区域包</button><button :disabled="busy" @click="discardContours">取消预览</button></div></template>
        </section>
        <section class="ne-search"><label for="ne-search">定位节点或路段</label><input id="ne-search" v-model="search" placeholder="输入 ID、道路组或 OSM 编号" />
          <div v-if="search.trim()" class="ne-results"><button v-for="item in results" :key="`${item.type}:${item.id}`" @click="chooseSearchResult(item, $event)"><span>{{ item.type === 'node' ? '节点' : '路段' }}</span>{{ item.id }}</button><p v-if="!results.length">没有匹配结果</p></div>
        </section>
        <section class="ne-layers"><h2>图层</h2><label v-for="(value, key) in kinds" :key="key"><input v-model="visible[key]" type="checkbox" /><i :style="{ background: value.color }"></i>{{ value.label }}</label>
          <div class="ne-basemap-options"><label>辅助底图<select v-model="basemap" @change="changeBasemap"><option value="local">区域包本地轮廓</option><option value="baidu">百度地图</option></select></label><p v-if="basemapState === 'loading'" role="status">正在加载百度底图…</p><p v-if="basemapState === 'ready'">百度底图已叠加，路网编辑操作保持一致。</p><p v-if="basemapError" role="alert">{{ basemapError }}</p></div>
          <div class="ne-layer-extras"><label><input v-model="showNodes" type="checkbox" />{{ showToolNodes ? '节点（当前工具自动显示）' : '节点（放大可见）' }}</label><label><input v-model="showContext" type="checkbox" :disabled="basemapState === 'ready'" />本地建筑、水体与绿地</label><label><input v-model="showSource" type="checkbox" :disabled="basemapState === 'ready'" />本地道路中心线</label></div>
        </section>
        <section class="ne-package-panel"><h2>区域数据包</h2><p>{{ stats?.edges ? '框选一块路网，保存为可独立打开的区域包。' : '当前为空白草稿，先新增节点并连接步行通道。' }}</p><p v-if="packageBounds" class="ne-metric">选框 {{ Math.round(packageBounds.maxX-packageBounds.minX) }} × {{ Math.round(packageBounds.maxY-packageBounds.minY) }} 米</p><div><button :disabled="busy || !stats?.edges" @click="setTool('package')">{{ packageBounds ? '重新框选' : '框选区域' }}</button><button v-if="packageBounds" :disabled="busy" @click="previewPackage">打包选区</button></div></section>
        <section class="ne-properties">
          <h2>{{ selectedEdges.length > 1 ? `已选 ${selectedEdges.length} 条路段` : selection?.type === 'node' ? '节点属性' : selection?.type === 'edge' ? '路段属性' : '开始手动校对' }}</h2>
          <template v-if="selectedEdges.length > 1">
            <p>总长度 {{ selectedLength.toFixed(2) }} 米</p>
            <div class="ne-selected-list"><div v-for="edge in selectedPageRows" :key="edge.id"><button :disabled="busy" title="单独查看这条路段" @click="choose('edge', edge.id, true)"><i :style="{ background: kinds[edge.kind].color }"></i>{{ edge.id }}</button><button :disabled="busy" :aria-label="`取消选择 ${edge.id}`" @click="selectEdges(selectedEdgeIds.filter(id => id !== edge.id))">×</button></div></div>
            <div v-if="selectionPages > 1" class="ne-selection-pages"><button :disabled="selectionPage === 0" @click="selectionPage--">上一页</button><span>{{ selectionPage + 1 }} / {{ selectionPages }}</span><button :disabled="selectionPage + 1 >= selectionPages" @click="selectionPage++">下一页</button></div>
            <label>批量设置路段类型<select v-model="batchForm.kind"><option value="">保持各路段类型</option><option v-for="(value, key) in kinds" :key="key" :value="key">{{ value.label }}</option></select></label>
            <label>批量设置过街等待 / 秒<input v-model="batchForm.waitSeconds" type="number" min="0" step="1" placeholder="留空保持原等待时间" /></label>
            <p class="ne-small">等待时间仅应用到过街边。其他属性保持原值；切换类型时补齐必要字段。</p>
            <button :disabled="busy || (!batchForm.kind && batchForm.waitSeconds === '')" @click="applyBatch">批量应用属性</button>
            <button class="ne-danger" :disabled="busy" @click="askDelete">删除选中路段（{{ selectedEdges.length }}）</button>
          </template>
          <template v-else-if="selected">
            <code class="ne-id">{{ selected.id }}</code>
            <template v-if="selection.type === 'node'">
              <div class="ne-coordinate-form"><label>X / 米<input v-model="nodeForm.x" type="number" step="any" /></label><label>Y / 米<input v-model="nodeForm.y" type="number" step="any" /></label></div>
              <button :disabled="busy" @click="applyNode">更新坐标</button>
              <button :disabled="busy" @click="startFromNode(selected.id)">从此节点连线</button>
              <h3>相连路段 · {{ incident.length }}</h3><div class="ne-incidence"><button v-for="edge in incident" :key="edge.id" @click="choose('edge', edge.id)"><i :style="{ background: kinds[edge.kind].color }"></i>{{ edge.id }}</button></div>
            </template>
            <template v-else>
              <div class="ne-endpoints"><button @click="chooseNode(selected.from, true)"><span>起点</span>{{ selected.from }}</button><button @click="chooseNode(selected.to, true)"><span>终点</span>{{ selected.to }}</button></div>
              <label>路段类型<select v-model="edgeForm.kind"><option v-for="(value, key) in kinds" :key="key" :value="key">{{ value.label }}</option></select></label>
              <template v-if="edgeForm.kind === 'walkway'">
                <label>道路组<input v-model="edgeForm.streetBlockId" placeholder="为空时自动建立独立道路组" /></label>
                <label>接入约束<select v-model="edgeForm.accessMode"><option value="separated">分侧接入 · 不可跨街吸附</option><option value="shared">共享空间 · 保留横向接入</option></select></label>
              </template>
              <label v-if="edgeForm.kind === 'walkway' && edgeForm.accessMode === 'separated'">道路侧<select v-model="edgeForm.side"><option value="left">左侧</option><option value="right">右侧</option></select></label>
              <template v-if="edgeForm.kind === 'walkway' && edgeForm.accessMode === 'shared'"><label>宽度 / 米<input v-model="edgeForm.widthMeters" type="number" min="0.1" step="0.1" /></label><label>共享空间<select v-model="edgeForm.sharedWayType"><option value="shared_alley">共享小巷</option><option value="pedestrian_street">步行街</option></select></label></template>
              <label v-if="edgeForm.kind === 'crossing'">过街等待 / 秒<input v-model="edgeForm.waitSeconds" type="number" min="0" step="1" /></label>
              <p class="ne-metric">长度 {{ pathLength(selected.pathMeters).toFixed(2) }} 米 · {{ selected.pathMeters.length }} 个形状点</p>
              <button :disabled="busy" @click="applyEdge">应用属性</button><button :disabled="busy" @click="setTool('shape')">添加形状点</button>
              <p class="ne-small">选中路段后拖动白色形状点调整线形；粉色端点使用“移动节点”。</p>
            </template>
            <p v-if="selection.type === 'node'" class="ne-small">删除节点时会一起移除 {{ incident.length }} 条相连路段，并清理失去连接的端点，可用撤销恢复。</p>
            <button class="ne-danger" :disabled="busy" @click="askDelete">{{ selection.type === 'node' ? '删除节点' : '删除路段' }}</button>
          </template>
          <template v-else><p>放大到要修改的街口，点击节点或线条。</p><p>错误连线可直接删除；缺失连接用“连线”补入。接入路段中间时，先拆分路段。</p><p class="ne-small">线条相交、节点重合都不会自动连通。每条新连接由你明确建立。</p></template>
        </section>
        <section v-if="route" class="ne-route"><h2>路径检查</h2><strong>{{ route.seconds.toFixed(1) }} 秒</strong><p>步速 1.3 米/秒，过街等待 {{ route.waitSeconds }} 秒。</p><ol><li v-for="edge in route.edges" :key="edge.id"><button @click="choose('edge', edge.id, true)">{{ edge.id }}</button></li></ol><button @click="route = null; redraw()">清除路径</button></section>
        <section class="ne-summary"><h2>当前路网</h2><p v-if="stats">{{ stats.nodes.toLocaleString() }} 个节点 / {{ stats.edges.toLocaleString() }} 条路段</p><p v-if="audit">{{ audit.componentCount }} 个连通分量 · {{ audit.isolatedNodes }} 个孤立节点<span v-if="engine"> · C++ {{ engine }} 检查通过</span></p><p v-else-if="dirty">修改后尚未检查</p><p class="ne-small">合成数据，人工编辑继续标为未核实。连通分量和孤立点只作提示。</p><button :disabled="busy" @click="reload">重新加载文件</button></section>
      </aside>
    </div>
    <footer class="ne-status" role="status"><span>{{ message }}</span><code>X {{ pointer[0].toFixed(2) }} / Y {{ pointer[1].toFixed(2) }} 米</code></footer>
    <div v-if="contourPrompt" class="ne-modal-backdrop">
      <section class="ne-dialog ne-package-dialog" role="dialog" aria-modal="true" aria-labelledby="ne-contour-title">
        <h2 id="ne-contour-title">生成本地轮廓</h2>
        <p v-if="contourBounds">框选范围 {{ Math.round(contourBounds.maxX-contourBounds.minX) }} × {{ Math.round(contourBounds.maxY-contourBounds.minY) }} 米。</p>
        <p>从 OpenStreetMap 获取建筑、水体、绿地和道路示意，生成后在地图上预览，再选择是否保存到当前区域包。</p>
        <p class="ne-small">建议选择几平方公里的街区。单边最多 6 公里，面积最多 25 平方公里；地物数量取决于当地 OSM 数据覆盖。</p>
        <p v-if="busy" role="status">正在下载地物并校准坐标，请稍候…</p><p v-if="error" class="ne-library-error" role="alert">{{ error }}</p>
        <div class="ne-package-actions"><button :disabled="busy" @click="contourPrompt = false">取消</button><button class="ne-primary" :disabled="busy" @click="generateContours">{{ busy ? '正在生成…' : '开始生成轮廓' }}</button></div>
      </section>
    </div>
    <div v-if="packagePrompt" class="ne-modal-backdrop">
      <section class="ne-dialog ne-package-dialog" role="dialog" aria-modal="true" aria-labelledby="ne-package-title">
        <h2 id="ne-package-title">{{ packageResult ? '区域包已生成' : '打包框选区域' }}</h2>
        <template v-if="packageResult">
          <p>{{ packageResult.name }} · v{{ packageResult.version }}<br />{{ packageResult.nodeCount }} 个节点 / {{ packageResult.edgeCount }} 条路段<br />ZIP {{ (packageResult.archiveBytes/1024).toFixed(0) }} KB，C++ {{ packageResult.engine }} 检查通过。</p>
          <p>已加入区域列表，原区域和画布修改可继续保留。</p>
          <div><button @click="packagePrompt = false">继续当前编辑</button><a class="ne-button" :href="packageResult.downloadUrl" :download="packageResult.archiveName">下载 ZIP</a><button class="ne-primary" @click="chooseRegion(packageResult.id)">打开新区域</button></div>
        </template>
        <form v-else @submit.prevent="buildPackage">
          <p v-if="!packagePreview" role="status">正在裁剪并检查路网…</p>
          <div v-else class="ne-package-counts"><strong>{{ packagePreview.edgeCount }}<span>条路段</span></strong><strong>{{ packagePreview.nodeCount }}<span>个节点</span></strong><strong>{{ packagePreview.boundaryNodeCount }}<span>个裁剪端点</span></strong></div>
          <label>区域名称<input v-model="packageForm.name" maxlength="80" required :disabled="busy" /></label>
          <div class="ne-package-fields"><label>区域标识<input v-model="packageForm.id" pattern="[a-z0-9][a-z0-9_-]{0,79}" maxlength="80" required :disabled="busy" /></label><label>版本<input v-model="packageForm.version" pattern="[0-9]+\.[0-9]+\.[0-9]+" required :disabled="busy" /></label></div>
          <p class="ne-small">包含当前画布修改、路网、本地底图、百度校准和编辑记录。路段按选框裁剪，端点不会自动连接。原区域不会被覆盖。</p>
          <p v-if="packagePreview?.excludedPartialConnections" class="ne-package-warning">{{ packagePreview.excludedPartialConnections }} 条跨出选框的过街或转弯连接将排除；需要保留时请扩大选框。</p>
          <p class="ne-small">边缘分析可能受数据截断影响；POI 缓存和 API 密钥不包含在包内。</p>
          <p v-if="error" class="ne-library-error" role="alert">{{ error }}</p>
          <div class="ne-package-actions"><button type="button" :disabled="busy" @click="packagePrompt = false">取消</button><button type="submit" class="ne-primary" :disabled="busy || !packagePreview">{{ busy ? '正在生成…' : '生成并下载区域包' }}</button></div>
        </form>
      </section>
    </div>
    <div v-if="deletePrompt" class="ne-modal-backdrop" @click.self="deletePrompt = null">
      <section class="ne-dialog" role="dialog" aria-modal="true" aria-labelledby="ne-delete-title">
        <h2 id="ne-delete-title">{{ deletePrompt.type === 'node' ? '删除这个节点？' : deletePrompt.count > 1 ? `删除选中的 ${deletePrompt.count} 条路段？` : '删除这条路段？' }}</h2>
        <div v-if="deletePrompt.type === 'edges'" class="ne-delete-ids"><code v-for="id in deletePrompt.ids.slice(0, 200)" :key="id">{{ id }}</code><small v-if="deletePrompt.ids.length > 200">另有 {{ deletePrompt.ids.length - 200 }} 条路段，确认后一起删除。</small></div>
        <code v-else>{{ deletePrompt.id }}</code>
        <p>{{ deletePrompt.type === 'node' ? `会同时删除 ${deletePrompt.count} 条相连路段，并清理 ${deletePrompt.nodeIds.length} 个其他孤立端点。` : `会同时删除 ${deletePrompt.count} 条路段和 ${deletePrompt.nodeIds.length} 个孤立端点。` }}可以用撤销恢复。</p>
        <div v-if="deletePrompt.type === 'node' && deletePrompt.edgeIds.length" class="ne-delete-ids"><code v-for="id in deletePrompt.edgeIds.slice(0, 200)" :key="id">{{ id }}</code><small v-if="deletePrompt.edgeIds.length > 200">另有 {{ deletePrompt.edgeIds.length - 200 }} 条相连路段，确认后一起删除。</small></div>
        <div><button @click="deletePrompt = null">取消</button><button class="ne-danger" @click="removeSelected">确认删除</button></div>
      </section>
    </div>
  </main>
</template>
