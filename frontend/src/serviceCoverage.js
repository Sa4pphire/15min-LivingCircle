export const serviceLabels = { market: '市场', pharmacy: '药店', primary_school: '小学' };

export async function requestServiceCoverage(context, { online = false, signal } = {}) {
  const path = context.mode === 'real' ? 'sampled-analyses' : 'analyses';
  const query = new URLSearchParams({ revision: context.resultRevision, online });
  const response = await fetch(`/api/v1/${path}/${encodeURIComponent(context.analysisId)}/service-coverage?${query}`,
    { method: 'POST', signal });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail?.message || '服务覆盖查询未完成');
  return payload;
}

export function coverageFeatures(coverage, category = 'all', estimate = false) {
  if (!coverage) return [];
  if (!coverage.complete && !(estimate && coverage.canEstimate)) {
    return [{ type: 'Feature', geometry: coverage.domain,
      properties: { unknown: true, missingCategories: [], confidence: 'unknown' } }];
  }
  const layer = coverage.complete ? coverage.assessed : coverage.estimated;
  return (layer?.features ?? []).filter(feature => category === 'all'
    || feature.properties.missingCategories.includes(category));
}

export function coverageColor(properties, category = 'all') {
  if (properties.unknown) return '#7b878b';
  if (category !== 'all') return { market: '#b78b19', pharmacy: '#377eac', primary_school: '#8764b0' }[category];
  return ({ 1: '#d2aa32', 2: '#e48636', 3: '#d3484a' })[properties.missingCategories?.length] || '#7b878b';
}

export function coverageDescription(properties) {
  if (properties.unknown) return '数据不足，无法判定服务缺失；灰色不代表盲区。';
  return `缺少：${properties.missingCategories.map(key => serviceLabels[key]).join('、')}；直线 1 公里覆盖；${properties.confidence === 'estimate' ? '现有地点估算，清单不完整' : '检索分页已完成，设施清单仍未经实地核实'}。`;
}
