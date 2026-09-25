export const mapZoomTiers = [
  { id: "large", label: "大", hint: "近景", description: "大比例尺，放大查看并拖动地图", factor: 3.1 },
  { id: "medium", label: "中", hint: "街区", description: "中比例尺，查看街区并拖动地图", factor: 1.5 },
  { id: "small", label: "小", hint: "全览", description: "小比例尺，查看完整范围", factor: 1 },
];

export function zoomFactor(tier) {
  return mapZoomTiers.find((item) => item.id === tier)?.factor ?? 1;
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
