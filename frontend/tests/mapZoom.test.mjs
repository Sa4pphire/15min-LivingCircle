import test from "node:test";
import assert from "node:assert/strict";
import {
  baiduZoomForTier,
  clampMapPan,
  mapZoomTiers,
  syntheticViewBox,
  zoomedFit,
} from "../src/mapZoom.js";

test("three named scales use the requested 3.1×, 1.5× and 1× factors", () => {
  assert.deepEqual(mapZoomTiers.map((tier) => tier.id), ["large", "medium", "small"]);
  assert.deepEqual(mapZoomTiers.map((tier) => tier.factor), [3.1, 1.5, 1]);
  assert.equal(syntheticViewBox("small"), "-90 0 1080 620");
  const largeWidth = Number(syntheticViewBox("large").split(" ")[2]);
  const mediumWidth = Number(syntheticViewBox("medium").split(" ")[2]);
  assert.ok(Math.abs(largeWidth - 1080 / 3.1) < 1e-9);
  assert.equal(mediumWidth, 720);
});

test("fallback projection centres the selected point at large scale", () => {
  const fit = { scale: 2, translateX: 300, translateY: 220 };
  const focus = [40, -30];
  const large = zoomedFit(fit, 1000, 600, "large", focus);
  assert.equal(large.scale, 6.2);
  assert.equal(large.translateX + focus[0] * large.scale, 500);
  assert.equal(large.translateY + focus[1] * large.scale, 300);
  const medium = zoomedFit(fit, 1000, 600, "medium", focus);
  assert.equal(medium.scale, 3);
  assert.deepEqual(zoomedFit(fit, 1000, 600, "small"), fit);
});

test("pan limits keep a dragged view near its demonstration area", () => {
  assert.equal(clampMapPan(400, 120), 120);
  assert.equal(clampMapPan(-400, 120), -120);
  assert.equal(clampMapPan(35, 120), 35);
  assert.equal(syntheticViewBox("small", null, { x: 40, y: -20 }), "-50 -20 1080 620");
});

test("Baidu tiers preserve ordering and respect map zoom bounds", () => {
  const large = baiduZoomForTier(14, "large");
  const medium = baiduZoomForTier(14, "medium");
  const small = baiduZoomForTier(14, "small");
  assert.ok(large > medium && medium > small);
  assert.equal(baiduZoomForTier(20.9, "large", 3, 21), 21);
  assert.equal(baiduZoomForTier(14, "small"), 14);
  assert.equal(baiduZoomForTier(2.9, "small", 3, 21), 3);
});
