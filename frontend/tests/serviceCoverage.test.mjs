import test from 'node:test';
import assert from 'node:assert/strict';
import { coverageFeatures, coverageColor, requestServiceCoverage } from '../src/serviceCoverage.js';
const domain = { type: 'Polygon', coordinates: [[[0,0],[2,0],[2,2],[0,0]]] };
const zone = missingCategories => ({ geometry: domain, properties: { missingCategories } });

test('incomplete and empty inventory remains gray, never red', () => {
  const [feature] = coverageFeatures({ domain, complete: false, canEstimate: false }, 'all', true);
  assert.equal(feature.properties.unknown, true);
  assert.equal(coverageColor(feature.properties), '#7b878b');
});
test('estimate requires opt-in and category filters retain holes/geometry', () => {
  const features = [zone(['market']), zone(['pharmacy', 'primary_school'])];
  const value = { domain, complete: false, canEstimate: true, estimated: { features } };
  assert.equal(coverageFeatures(value)[0].properties.unknown, true);
  assert.deepEqual(coverageFeatures(value, 'pharmacy', true), [features[1]]);
  assert.equal(coverageFeatures(value, 'pharmacy', true)[0].geometry, domain);
});
test('red is reserved for three missing services', () => {
  assert.equal(coverageColor(zone(['market', 'pharmacy', 'primary_school']).properties), '#d3484a');
  assert.notEqual(coverageColor(zone(['market']).properties), '#d3484a');
  assert.notEqual(coverageColor(zone(['market', 'pharmacy']).properties), '#d3484a');
});
test('coverage targets active mode and its analysis revision', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return { ok: true, json: async () => ({}) }; };
  try {
    await requestServiceCoverage({ mode: 'synthetic', analysisId: 'cpp-id', resultRevision: 2 });
    await requestServiceCoverage({ mode: 'real', analysisId: 'real-id', resultRevision: 1 }, { online: true });
    assert.equal(calls[0][0], '/api/v1/analyses/cpp-id/service-coverage?revision=2&online=false');
    assert.equal(calls[1][0], '/api/v1/sampled-analyses/real-id/service-coverage?revision=1&online=true');
  } finally { globalThis.fetch = original; }
});
