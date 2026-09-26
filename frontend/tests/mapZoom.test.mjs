import test from "node:test";
import assert from "node:assert/strict";
import {
  baiduZoomForTier,
  clampMapPan,
  mapZoomTiers,
  markerScaleForTier,
  syntheticViewBox,
  zoomedFit,
} from "../src/mapZoom.js";

test("three named scales use the requested 5×, 3× and 1.5× factors", () => {
  assert.deepEqual(mapZoomTiers.map((tier) => tier.id), ["large", "medium", "small"]);
  assert.deepEqual(mapZoomTiers.map((tier) => tier.factor), [5, 3, 1.5]);
  const small = syntheticViewBox("small").split(" ").map(Number);
  assert.ok(Math.abs(small[2] - 1080 / 1.5) < 1e-9);
  assert.ok(Math.abs(small[3] - 620 / 1.5) < 1e-9);
  const largeWidth = Number(syntheticViewBox("large").split(" ")[2]);
  const mediumWidth = Number(syntheticViewBox("medium").split(" ")[2]);
  assert.equal(largeWidth, 216);
  assert.equal(mediumWidth, 360);
});

test("fallback projection centres the selected point at large scale", () => {
  const fit = { scale: 2, translateX: 300, translateY: 220 };
  const focus = [40, -30];
  const large = zoomedFit(fit, 1000, 600, "large", focus);
  assert.equal(large.scale, 10);
  assert.equal(large.translateX + focus[0] * large.scale, 500);
  assert.equal(large.translateY + focus[1] * large.scale, 300);
  const medium = zoomedFit(fit, 1000, 600, "medium", focus);
  assert.equal(medium.scale, 6);
  assert.equal(zoomedFit(fit, 1000, 600, "small").scale, 3);
});

test("selected origin marker scales with the map relative to the medium tier", () => {
  assert.equal(markerScaleForTier("large"), 5 / 3);
  assert.equal(markerScaleForTier("medium"), 1);
  assert.equal(markerScaleForTier("small"), 0.5);
});

test("pan limits keep a dragged view near its demonstration area", () => {
  assert.equal(clampMapPan(400, 120), 120);
  assert.equal(clampMapPan(-400, 120), -120);
  assert.equal(clampMapPan(35, 120), 35);
  const panned = syntheticViewBox("small", null, { x: 40, y: -20 }).split(" ").map(Number);
  assert.equal(panned[0], 130);
  assert.ok(Math.abs(panned[1] - (290 - 620 / 1.5 / 2)) < 1e-9);
});

test("Baidu tiers preserve ordering and respect map zoom bounds", () => {
  const large = baiduZoomForTier(14, "large");
  const medium = baiduZoomForTier(14, "medium");
  const small = baiduZoomForTier(14, "small");
  assert.ok(large > medium && medium > small);
  assert.equal(baiduZoomForTier(20.9, "large", 3, 21), 21);
  assert.ok(Math.abs(baiduZoomForTier(14, "small") -
    (14 + Math.log2(1.5))) < 1e-9);
  assert.equal(baiduZoomForTier(2, "small", 3, 21), 3);
});
