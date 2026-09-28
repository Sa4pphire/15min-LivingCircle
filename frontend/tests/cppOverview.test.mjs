import test from "node:test";
import assert from "node:assert/strict";
import { cppOverviewFit, cppOverviewInsets, cppOverviewPoints } from "../src/cppOverview.js";

const result = { origin: { x: 0, y: 0 }, displayArea: { geometry: { type: "MultiPolygon",
  coordinates: [[[[-500, -300], [700, -300], [700, 800], [-500, -300]]]] } },
  poiFacilities: [
    { id: "school", category: "education", point: [600, 750], insideDisplayPolygon: true },
    { id: "outside", category: "healthcare", point: [5000, 5000], insideDisplayPolygon: false },
  ] };

test("overview fits actual C++ rings, origin and circle POIs without including outside candidates", () => {
  const points = cppOverviewPoints(result);
  assert.ok(points.some(point => point[0] === 600 && point[1] === 750));
  assert.ok(points.some(point => point[0] === 0 && point[1] === 0));
  assert.ok(!points.some(point => point[0] === 5000));
  assert.equal(cppOverviewFit(null, 320, 500), null);
});

for (const [width, height] of [[320, 500], [1280, 700]]) {
  test(`overview leaves space for filters, zoom buttons and legend at ${width}×${height}`, () => {
    const fit = cppOverviewFit(result, width, height);
    const inset = cppOverviewInsets(width, height);
    for (const [x, y] of cppOverviewPoints(result)) {
      const pixel = [x * fit.scale + fit.translateX, y * fit.scale + fit.translateY];
      assert.ok(pixel[0] > inset.left && pixel[0] < width - inset.right);
      assert.ok(pixel[1] > inset.top && pixel[1] < height - inset.bottom);
    }
  });
}
