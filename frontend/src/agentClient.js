// Only the application backend knows the model endpoint and API key.
export class AgentClientError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.name = 'AgentClientError';
    this.code = code;
    this.status = status;
  }
}

export function validAgentContext(context) {
  return typeof context?.regionId === 'string' && Boolean(context.regionId)
    && ['real', 'synthetic'].includes(context.mode)
    && typeof context.analysisId === 'string' && Boolean(context.analysisId)
    && Number.isInteger(context.resultRevision) && context.resultRevision >= 0;
}

export function sameAgentContext(first, second) {
  return validAgentContext(first) && validAgentContext(second)
    && ['regionId', 'mode', 'analysisId', 'resultRevision'].every(key => first[key] === second[key]);
}

async function readJson(response) {
  let data;
  try {
    data = await response.json();
  } catch {
    throw new AgentClientError('AGENT_INVALID_RESPONSE', '助手服务返回了无法读取的响应。', response.status);
  }
  if (!response.ok) {
    throw new AgentClientError(data?.detail?.code || 'AGENT_HTTP_ERROR',
      typeof data?.detail?.message === 'string' ? data.detail.message : '助手请求未完成，请稍后重试。', response.status);
  }
  return data;
}

async function requestJson(path, options, { signal, fetchImpl = fetch, timeoutMs }) {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    return await readJson(await fetchImpl(path, { ...options, signal: controller.signal }));
  } catch (error) {
    if (timedOut) {
      throw new AgentClientError('AGENT_CLIENT_TIMEOUT', '等待助手响应超时，请稍后重试。');
    }
    if (controller.signal.aborted) throw new DOMException('Assistant request cancelled', 'AbortError');
    if (error instanceof AgentClientError) throw error;
    throw new AgentClientError('AGENT_NETWORK_ERROR', '暂时无法连接助手服务，请检查后端是否已启动。');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

export async function requestAgentStatus(options = {}) {
  const data = await requestJson('/api/v1/agent/status', { cache: 'no-store' }, { ...options, timeoutMs: 10_000 });
  if (typeof data?.enabled !== 'boolean' || typeof data.configured !== 'boolean') {
    throw new AgentClientError('AGENT_INVALID_RESPONSE', '助手配置状态无法读取。');
  }
  return { enabled: data.enabled, configured: data.configured };
}

export async function requestAgentChat({ message, conversationId, context }, options = {}) {
  if (!validAgentContext(context)) {
    throw new AgentClientError('ANALYSIS_NOT_READY', '请先在地图选点并完成一次生活圈分析。');
  }
  const data = await requestJson('/api/v1/agent/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversationId: conversationId || null, context }),
  }, { ...options, timeoutMs: 110_000 });
  if (!sameAgentContext(data?.context, context) || typeof data.conversationId !== 'string' || !data.conversationId
      || typeof data.reply !== 'string' || !data.reply.trim() || !Array.isArray(data.facilities)
      || data.facilities.length > 30 || data.facilities.some(item => typeof item?.id !== 'string' || !item.id
        || typeof item.name !== 'string' || !item.name)) {
    throw new AgentClientError('AGENT_INVALID_RESPONSE', '助手返回的回答与当前分析不一致，请重新提问。');
  }
  if (data.mapAction != null && (data.mapAction.type !== 'show_route'
      || typeof data.mapAction.poiId !== 'string' || !data.mapAction.poiId
      || data.mapAction.route?.poiId !== data.mapAction.poiId
      || data.mapAction.route?.analysisId !== context.analysisId || data.mapAction.route.status !== 'ready')) {
    throw new AgentClientError('AGENT_INVALID_RESPONSE', '助手返回的路线与当前分析不一致。');
  }
  return data;
}
