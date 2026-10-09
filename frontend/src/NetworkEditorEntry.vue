<script setup>
import { onMounted, ref } from 'vue';
import { loadActiveRegion } from './regionLoader.js';
import NetworkEditor from './NetworkEditor.vue';
import { locateEditorRegion } from './editorBaiduMap.js';
import './networkEditor.css';

const packages = ref([]), opened = ref(null), selected = ref('');
const loading = ref(true), opening = ref(false), error = ref('');
const creating = ref(false), showCreate = ref(false), createStage = ref('');
const newLocation = ref(''), newName = ref('');
async function create() {
  if (creating.value || !newLocation.value.trim()) return;
  creating.value = true; error.value = ''; createStage.value = '正在定位地点…';
  try {
    const location = newLocation.value.trim();
    const centerBd09 = await locateEditorRegion(location, import.meta.env.VITE_BAIDU_BROWSER_AK?.trim());
    createStage.value = '正在创建区域…';
    const response = await fetch('/api/v1/network-editor/regions', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location, name: (newName.value.trim() || location).slice(0,80), centerBd09 }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || '区域创建失败，请重试');
    selected.value = data.id;
    await open(data.id);
  } catch (exc) { error.value = exc.message; }
  finally { creating.value = false; createStage.value = ''; }
}
async function list() {
  loading.value = true; error.value = ''; packages.value = [];
  try {
    const response = await fetch('/api/v1/network-editor/regions', { cache: 'no-cache' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || '区域目录无法读取，请启动本地编辑器服务');
    packages.value = data.items;
    const preferred = new URLSearchParams(location.search).get('region') || selected.value || data.defaultRegionId;
    selected.value = data.items.find(item => item.id === preferred && !item.error)?.id || data.items.find(item => !item.error)?.id || '';
  } catch (exc) { error.value = exc.message; }
  finally { loading.value = false; }
}
async function open(identity = selected.value) {
  opening.value = true; error.value = '';
  try {
    const region = await loadActiveRegion(fetch, identity);
    opened.value = region;
    const url = new URL(location.href); url.searchParams.set('region', identity);
    history.replaceState(null, '', url);
  } catch (exc) { error.value = exc.message; }
  finally { opening.value = false; }
}
async function chooseRegion(identity) {
  opened.value = null;
  if (identity) { selected.value = identity; await open(identity); }
  else await list();
}
function size(item) {
  const b = item.boundsMeters;
  return `${((b.maxX-b.minX)/1000).toFixed(2)} × ${((b.maxY-b.minY)/1000).toFixed(2)} 公里`;
}
onMounted(list);
</script>

<template>
  <NetworkEditor v-if="opened" :key="opened.id" @choose-region="chooseRegion" />
  <main v-else class="ne-library">
    <header><a href="/?mode=synthetic">返回生活圈</a><span>路网编辑器</span></header>
    <div class="ne-library-body">
      <section class="ne-library-intro"><span class="ne-library-symbol" aria-hidden="true">▧</span><h1>选择要处理的区域</h1><p>打开已有区域继续校对，或输入地点创建一份新的路网。</p><div class="ne-library-buttons"><button :disabled="loading || opening || creating" @click="list">刷新区域列表</button><button class="ne-primary" :disabled="opening || creating" @click="showCreate = !showCreate">创建新区域包</button></div>
        <form v-if="showCreate" class="ne-create-form" @submit.prevent="create"><h2>从一个地点开始</h2><label>大概位置<input v-model="newLocation" placeholder="例如：广东广州、广州天河区" minlength="2" maxlength="120" required :disabled="creating" /></label><label>区域名称（可选）<input v-model="newName" placeholder="默认使用输入的地点" maxlength="80" :disabled="creating" /></label><p>百度底图会定位到这个地点。进入后新增节点、连接步行通道，保存为自己的区域包。</p><button class="ne-primary" type="submit" :disabled="creating || !newLocation.trim()">{{ creating ? createStage : '创建并打开编辑器' }}</button><p v-if="error" class="ne-library-error" role="alert">{{ error }}</p></form>
      </section>
      <section class="ne-region-list" aria-label="现有区域包">
        <p v-if="loading" role="status">正在读取区域目录…</p>
        <p v-else-if="!packages.length">还没有区域包。将已有区域包解压到项目的 data/regions 目录后刷新。</p>
        <label v-for="item in packages" :key="item.id" class="ne-region-row" :class="{ selected: selected === item.id, invalid: item.error }">
          <input v-model="selected" type="radio" name="region" :value="item.id" :disabled="Boolean(item.error) || opening || loading" />
          <span><strong>{{ item.name }}<b v-if="item.status === 'draft'" class="ne-draft-badge">草稿</b></strong><small>{{ item.id }}<template v-if="item.version"> / v{{ item.version }}</template></small><em v-if="item.error">{{ item.error }}</em><p v-else>{{ item.nodeCount.toLocaleString() }} 个节点 · {{ item.edgeCount.toLocaleString() }} 条路段<template v-if="item.status !== 'draft'"> · {{ size(item) }}</template></p></span>
        </label>
        <p v-if="error && !showCreate" class="ne-library-error" role="alert">{{ error }}</p>
        <footer><span>打开区域只作用于本编辑器。</span><button class="ne-primary" :disabled="!selected || loading || opening || creating" @click="open()">{{ opening ? '正在打开…' : '打开区域' }}</button></footer>
      </section>
    </div>
  </main>
</template>
