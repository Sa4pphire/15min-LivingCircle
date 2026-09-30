export const mapZoomTiers = [
  { id: "large", label: "大", hint: "5×", description: "大比例尺，5 倍放大并可拖动地图", factor: 5 },
  { id: "medium", label: "中", hint: "3×", description: "中比例尺，3 倍放大并可拖动地图", factor: 3 },
  { id: "small", label: "小", hint: "1.5×", description: "小比例尺，1.5 倍放大", factor: 1.5 },
];

export function zoomFactor(tier) {
  return mapZoomTiers.find((item) => item.id === tier)?.factor ?? 1;
}

// A discrete wheel step shares the button tiers; it never changes native SDK
// zoom directly. Accumulate trackpad pixels, then allow the 420 ms camera
// animation to settle before accepting the next step.
export function createMapWheelStepper() {
  let distance = 0;
  let lastEventAt = -Infinity;
  let nextStepAt = -Infinity;
  return {
    reset() {
      distance = 0;
      lastEventAt = -Infinity;
      nextStepAt = -Infinity;
    },
    step(tier, event, now, pageHeight = 800) {
      const deltaY = event.deltaY;
      if (event.ctrlKey || !Number.isFinite(deltaY) || deltaY === 0 ||
        Math.abs(event.deltaX ?? 0) > Math.abs(deltaY)) return null;
      const delta = deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? pageHeight : 1);
      if (now < nextStepAt) {
        lastEventAt = now;
        distance = 0;
        return null;
      }
      if (now - lastEventAt > 180 || Math.sign(delta) !== Math.sign(distance)) distance = 0;
      lastEventAt = now;
      distance += delta;
      if (Math.abs(distance) < 48) return null;
      distance = 0;
      nextStepAt = now + 460;
      const index = mapZoomTiers.findIndex(item => item.id === tier);
      // "Full circle" is a fitted view, not a fourth fixed zoom tier.
      if (index < 0) return delta < 0 ? "medium" : "small";
      const next = Math.max(0, Math.min(mapZoomTiers.length - 1, index + (delta < 0 ? -1 : 1)));
      return mapZoomTiers[next].id;
    },
  };
}

export function markerScaleForTier(tier) {
  return zoomFactor(tier) / zoomFactor("medium");
}

export function zoomedFit(fit, width, height, tier, focus = null) {
  const scale = fit.scale * zoomFactor(tier);
  const baseCenter = [
    (width / 2 - fit.translateX) / fit.scale,
    (height / 2 - fit.translateY) / fit.scale,
  ];
  const [x, y] = tier === "large" && focus ? focus : baseCenter;
  return {
    scale,
    translateX: width / 2 - x * scale,
    translateY: height / 2 - y * scale,
  };
}

export function syntheticViewBox(tier, focus = null, pan = { x: 0, y: 0 }) {
  const factor = zoomFactor(tier);
  const width = 1080 / factor;
  const height = 620 / factor;
  const [centerX, centerY] = tier === "large" && focus
    ? [focus.x, focus.y] : [450, 310];
  return `${centerX - width / 2 + pan.x} ${centerY - height / 2 + pan.y} ${width} ${height}`;
}

export function clampMapPan(value, limit) {
  return Math.max(-limit, Math.min(limit, value));
}

export function baiduZoomForTier(fittedZoom, tier, minZoom = 3, maxZoom = 21) {
  const requested = fittedZoom + Math.log2(zoomFactor(tier));
  return Math.min(maxZoom, Math.max(minZoom, requested));
}
