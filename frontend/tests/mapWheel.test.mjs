import test from "node:test";
import assert from "node:assert/strict";
import { createMapWheelStepper } from "../src/mapZoom.js";

const wheel = (deltaY, overrides = {}) => ({ deltaY, deltaX: 0, deltaMode: 0, ...overrides });

test("wheel up enlarges and wheel down reduces one named tier, clamping at both limits", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("small", wheel(-120), 0), "medium");
  assert.equal(stepper.step("medium", wheel(-120), 500), "large");
  assert.equal(stepper.step("large", wheel(-120), 1000), "large");
  assert.equal(stepper.step("large", wheel(120), 1500), "medium");
  assert.equal(stepper.step("medium", wheel(120), 2000), "small");
  assert.equal(stepper.step("small", wheel(120), 2500), "small");
});

test("pixel, line and page wheel units use the same discrete step", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("medium", wheel(-48), 0), "large");
  assert.equal(stepper.step("medium", wheel(3, { deltaMode: 1 }), 500), "small");
  assert.equal(stepper.step("medium", wheel(-0.1, { deltaMode: 2 }), 1000, 480), "large");
});

test("trackpad deltas accumulate and an animation cooldown prevents rapid multi-tier jumps", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("small", wheel(-16), 0), null);
  assert.equal(stepper.step("small", wheel(-16), 20), null);
  assert.equal(stepper.step("small", wheel(-16), 40), "medium");
  assert.equal(stepper.step("medium", wheel(-120), 60), null);
  assert.equal(stepper.step("medium", wheel(-120), 200), null);
  assert.equal(stepper.step("medium", wheel(-120), 499), null);
  assert.equal(stepper.step("medium", wheel(-48), 500), "large");
});

test("direction reversal and idle gaps discard stale partial scrolling", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("medium", wheel(-32), 0), null);
  assert.equal(stepper.step("medium", wheel(24), 40), null);
  assert.equal(stepper.step("medium", wheel(24), 60), "small");
  stepper.reset();
  assert.equal(stepper.step("medium", wheel(-32), 1000), null);
  assert.equal(stepper.step("medium", wheel(-24), 1300), null);
  assert.equal(stepper.step("medium", wheel(-24), 1320), "large");
});

test("horizontal scrolling, pinch/control zoom and empty deltas do not switch tiers", () => {
  const stepper = createMapWheelStepper();
  for (const event of [wheel(-120, { ctrlKey: true }), wheel(10, { deltaX: 80 }),
    wheel(0), wheel(NaN), wheel(Infinity)]) {
    assert.equal(stepper.step("medium", event, 0), null);
  }
});

test("reset clears accumulation and cooldown after explicit buttons or mode changes", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("medium", wheel(-120), 0), "large");
  stepper.reset();
  assert.equal(stepper.step("large", wheel(120), 30), "medium");
  stepper.reset();
  assert.equal(stepper.step("medium", wheel(24), 60), null);
  stepper.reset();
  assert.equal(stepper.step("medium", wheel(24), 80), null);
});

test("wheel exits full-circle fitting without adding a fourth fixed tier", () => {
  const stepper = createMapWheelStepper();
  assert.equal(stepper.step("result", wheel(-120), 0), "medium");
  assert.equal(stepper.step("result", wheel(120), 500), "small");
});
