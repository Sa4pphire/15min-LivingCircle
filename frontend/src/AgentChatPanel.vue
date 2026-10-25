<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { requestAgentChat, requestAgentStatus, sameAgentContext } from './agentClient.js';
import './agentChat.css';

const props = defineProps({
  context: { type: Object, default: null },
  resetKey: { type: String, required: true },
  regionName: { type: String, default: '当前区域' },
  routeError: { type: String, default: '' },
});
const emit = defineEmits(['show-route', 'reset']);
const opened = ref(false), input = ref(''), messages = ref([]), conversationId = ref(null);
const status = ref(null), statusLoading = ref(false), statusError = ref('');
const sending = ref(false), error = ref(''), inputElement = ref(null), messageList = ref(null);
let chatController, statusController, requestSequence = 0, statusSequence = 0, messageSequence = 0;
const shortcuts = ['附近有哪些医疗设施', '最近的医院怎么走'];
const configured = computed(() => status.value?.configured === true && status.value?.enabled === true);
const canSend = computed(() => configured.value && Boolean(props.context) && !sending.value);
const configurationNote = computed(() => {
  if (statusLoading.value) return '正在读取助手配置…';
  if (statusError.value) return statusError.value;
  if (!status.value) return '打开后可查看助手状态。';
  if (!status.value.configured) return '助手尚未配置模型 API，请先完成后端环境配置。';
  if (!status.value.enabled) return '助手尚未启用，请在后端环境配置中启用后重启服务。';
  return '';
});
const analysisLabel = computed(() => props.context
  ? `${props.regionName} · ${props.context.mode === 'real' ? '在线计算' : '合成模拟'}`
  : '先在地图选点并完成一次生活圈分析');

async function focusInput() {
  await nextTick();
  if (opened.value) inputElement.value?.focus();
}

async function scrollToLatest() {
  await nextTick();
  const list = messageList.value;
  if (list) list.scrollTop = list.scrollHeight;
}

async function refreshStatus() {
  statusController?.abort();
  const controller = new AbortController(), sequence = ++statusSequence;
  statusController = controller;
  statusLoading.value = true;
  statusError.value = '';
  try {
    const result = await requestAgentStatus({ signal: controller.signal });
    if (sequence === statusSequence && !controller.signal.aborted) {
      status.value = result;
      void focusInput();
    }
  } catch (failure) {
    if (sequence !== statusSequence || controller.signal.aborted) return;
    status.value = null;
    statusError.value = failure.message || '暂时无法读取助手配置。';
  } finally {
    if (sequence === statusSequence) {
      statusController = null;
      statusLoading.value = false;
    }
  }
}

function toggle() {
  opened.value = !opened.value;
  if (opened.value) {
    if (!status.value && !statusLoading.value) void refreshStatus();
    void focusInput();
    void scrollToLatest();
  }
}

function resetConversation() {
  requestSequence += 1;
  chatController?.abort();
  chatController = null;
  sending.value = false;
  conversationId.value = null;
  messages.value = [];
  input.value = '';
  error.value = '';
  emit('reset');
}

watch(() => props.resetKey, resetConversation, { flush: 'sync' });

function appendMessage(message) {
  // At most six question/answer pairs are retained by the panel.
  messages.value = [...messages.value, { id: ++messageSequence, ...message }].slice(-12);
  void scrollToLatest();
}

async function send(text = input.value, selectedPoiId = null) {
  const message = text.trim();
  if (!message || message.length > 2000 || !canSend.value) return;
  const context = { ...props.context }, sequence = ++requestSequence;
  const controller = new AbortController();
  chatController = controller;
  sending.value = true;
  input.value = '';
  error.value = '';
  emit('reset');
  appendMessage({ role: 'user', text: message });
  try {
    // Preserve an explicitly clicked card's identity without showing internal
    // IDs in the conversation. The server still checks analysis membership.
    const requestMessage = selectedPoiId ? `${message}\n当前已选设施ID：${selectedPoiId}` : message;
    const response = await requestAgentChat({ message: requestMessage, context, conversationId: conversationId.value },
      { signal: controller.signal });
    if (sequence !== requestSequence || controller.signal.aborted || !sameAgentContext(context, props.context)) return;
    conversationId.value = response.conversationId;
    appendMessage({ role: 'assistant', text: response.reply, facilities: response.facilities,
      comparison: response.comparison, action: response.mapAction, context });
    if (response.mapAction) emit('show-route', { context, action: response.mapAction });
  } catch (failure) {
    if (sequence !== requestSequence || controller.signal.aborted) return;
    error.value = failure.message || '这次提问没有完成，请稍后重试。';
    // A stale server session must not keep resolving old facility references.
    if (failure.status === 409 && !String(failure.code).includes('BUSY')) {
      conversationId.value = null;
      messages.value = [];
    }
  } finally {
    if (sequence === requestSequence) {
      chatController = null;
      sending.value = false;
      void scrollToLatest();
    }
  }
}

function showRoute(message) {
  if (!message.action || !sameAgentContext(message.context, props.context)) return;
  emit('show-route', { context: message.context, action: message.action });
}

function askRoute(facility) {
  if (typeof facility?.name === 'string' && typeof facility.id === 'string') {
    void send(`去${facility.name}怎么走？`, facility.id);
  }
}

function handleEnter(event) {
  if (event.isComposing) return;
  event.preventDefault();
  void send();
}

function facilityType(facility) {
  const labels = { community_health_center: '社区卫生服务中心', hospital: '医院', medical_facility: '医疗设施' };
  return labels[facility.facilityType] || facility.facilityType || (facility.category === 'healthcare' ? '医疗设施' : '地点');
}

function walkingTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  return seconds < 60 ? '步行不足 1 分钟' : `步行约 ${(seconds / 60).toFixed(1)} 分钟`;
}

function distance(meters) {
  if (!Number.isFinite(meters) || meters < 0) return '';
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${Math.round(meters)} 米`;
}

function comparisonNote(comparison) {
  if (!comparison) return '';
  const total = comparison.totalCandidates, checked = comparison.checkedCandidates, successful = comparison.successfulCandidates;
  if (![total, checked, successful].every(Number.isInteger)) return comparison.partial ? '比较尚未覆盖全部候选，结果仅限已成功查询的地点。' : '';
  return `${comparison.partial ? '部分比较' : '当前候选比较'}：已检查 ${checked}/${total} 个，成功查询 ${successful} 个。${comparison.partial ? '最短结果仅限已成功查询候选。' : '不代表现实设施清单完整。'}`;
}

onUnmounted(() => {
  requestSequence += 1;
  statusSequence += 1;
  chatController?.abort();
  statusController?.abort();
});
</script>

<template>
  <aside class="agent-chat" aria-label="生活圈助手" @wheel.stop @keydown.stop @pointerdown.stop @dblclick.stop>
    <section v-if="opened" id="life-agent-panel" class="agent-chat-panel" aria-labelledby="life-agent-title"
      @keydown.esc.prevent="opened = false">
      <header class="agent-chat-heading">
        <div><p>VCT ASSISTANT</p><h2 id="life-agent-title">生活圈助手</h2></div>
        <div class="agent-chat-heading-actions">
          <button type="button" @click="resetConversation(); focusInput()">新对话</button>
          <button type="button" class="agent-chat-close" aria-label="收起生活圈助手" @click="opened = false">×</button>
        </div>
      </header>
      <p class="agent-chat-context" :class="{ 'needs-analysis': !context }">{{ analysisLabel }}</p>
      <div v-if="configurationNote" class="agent-chat-configuration" role="status">
        <p>{{ configurationNote }}</p>
        <button type="button" :disabled="statusLoading" @click="refreshStatus">重新读取配置</button>
      </div>
      <div ref="messageList" class="agent-chat-messages" role="log" aria-label="助手对话记录" aria-live="polite" aria-relevant="additions text">
        <div v-if="!messages.length" class="agent-chat-welcome">
          <span class="agent-chat-welcome-mark" aria-hidden="true">↗</span>
          <h3>从这里出发，问问附近。</h3>
          <p>可以询问当前生活圈内的医疗设施，比较步行时间，并在地图上查看路线。</p>
          <p class="agent-chat-data-note">医疗设施包含社区卫生服务中心。比较只针对当前已取得的候选，路线和入口可能尚未核实。</p>
        </div>
        <article v-for="message in messages" :key="message.id" class="agent-chat-message" :class="`is-${message.role}`">
          <span class="agent-chat-author">{{ message.role === 'user' ? '你' : '生活圈助手' }}</span>
          <p class="agent-chat-message-text">{{ message.text }}</p>
          <p v-if="message.comparison" class="agent-chat-comparison">{{ comparisonNote(message.comparison) }}</p>
          <ul v-if="message.facilities?.length" class="agent-chat-facilities">
            <li v-for="facility in message.facilities" :key="facility.id" class="agent-chat-facility">
              <div class="agent-chat-facility-title"><strong>{{ facility.name }}</strong><span>{{ facilityType(facility) }}</span></div>
              <p v-if="facility.address" class="agent-chat-facility-address">{{ facility.address }}</p>
              <p v-if="walkingTime(facility.travelTimeSeconds)" class="agent-chat-facility-metrics">
                {{ walkingTime(facility.travelTimeSeconds) }}<span v-if="distance(facility.lengthMeters)"> · {{ distance(facility.lengthMeters) }}</span>
              </p>
              <p v-if="facility.withinThreshold === false" class="agent-chat-threshold">已超过本次 15 分钟预算</p>
              <p v-if="facility.message" class="agent-chat-facility-note">{{ facility.message }}</p>
              <button v-if="message.action?.poiId === facility.id" type="button" @click="showRoute(message)">显示路线 <span aria-hidden="true">↗</span></button>
              <button v-else type="button" :disabled="!canSend" @click="askRoute(facility)">询问路线 <span aria-hidden="true">→</span></button>
            </li>
          </ul>
          <button v-if="message.action && !message.facilities?.some(facility => facility.id === message.action.poiId)"
            class="agent-chat-route-button" type="button" @click="showRoute(message)">显示路线 ↗</button>
        </article>
        <p v-if="sending" class="agent-chat-pending" role="status">正在查询当前分析与步行路线…</p>
      </div>
      <div v-if="error || routeError" class="agent-chat-error" role="alert">{{ error || routeError }}</div>
      <div class="agent-chat-shortcuts" aria-label="快捷提问">
        <button v-for="question in shortcuts" :key="question" type="button" :disabled="!canSend" @click="send(question)">{{ question }}</button>
      </div>
      <form class="agent-chat-compose" @submit.prevent="send()">
        <label class="agent-chat-visually-hidden" for="life-agent-input">输入关于当前生活圈的问题</label>
        <textarea id="life-agent-input" ref="inputElement" v-model="input" rows="2" maxlength="2000"
          :disabled="!configured || !context" :placeholder="context ? '例如：最近的医院怎么走？' : '先选点并完成生活圈分析'"
          @keydown.enter.exact="handleEnter"></textarea>
        <button type="submit" :disabled="!canSend || !input.trim()">{{ sending ? '查询中' : '发送' }}</button>
      </form>
      <p class="agent-chat-bottom-note">根据当前分析回答 · 切换起点或模式后开启新对话</p>
    </section>
    <button type="button" class="agent-chat-launcher" :aria-expanded="opened" aria-controls="life-agent-panel" @click="toggle">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M20 11.5a7.6 7.6 0 0 1-8 7.5H8l-4 2v-5a7.3 7.3 0 0 1-1-4.5A7.6 7.6 0 0 1 11 4h1a7.6 7.6 0 0 1 8 7.5Z"/><path d="M7 11h9M7 14h6"/></svg>
      <span>{{ opened ? '收起助手' : '生活圈助手' }}</span>
    </button>
  </aside>
</template>
