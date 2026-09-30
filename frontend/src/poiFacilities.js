// Native Baidu coordinates stay BD-09. Preview points are the explicitly
// approximate graph-meter alignment, NEVER unlabelled WGS-84 coordinates.
export const poiCategoryStyles = {
  education: { label: "学校", glyph: "校", color: "#2f6e91" },
  healthcare: { label: "医院", glyph: "医", color: "#a53e50" },
  shopping: { label: "商超", glyph: "购", color: "#795d18" },
  public_service: { label: "公共服务", glyph: "公", color: "#68509b" },
  dining: { label: "餐饮", glyph: "餐", color: "#b45f2f" },
};

export function poiCandidates(result) {
  if (Array.isArray(result?.poiFacilities)) {
    return result.poiFacilities.filter(poi => poi && Object.hasOwn(poiCategoryStyles, poi.category));
  }
  const collection = result?.poiFacilities;
  if (collection?.coordType !== "bd09ll") return [];
  return (collection.features ?? []).filter((feature) =>
    feature.geometry?.type === "Point" &&
    Array.isArray(feature.geometry.coordinates) && feature.geometry.coordinates.length === 2 &&
    feature.geometry.coordinates.every(Number.isFinite) &&
    Math.abs(feature.geometry.coordinates[0]) <= 180 && Math.abs(feature.geometry.coordinates[1]) < 90 &&
    typeof feature.properties?.id === "string" && Object.hasOwn(poiCategoryStyles, feature.properties.category),
  ).map((feature) => {
    const point = feature.properties.localPointMeters;
    return { ...feature.properties, bd09: [...feature.geometry.coordinates],
      point: Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
        ? [point[0], -point[1]] : null };
  });
}

export function visiblePois(result, category = "all") {
  const local = result?.mode === "local_experiment";
  return poiCandidates(result).filter((poi) =>
    (local ? poi.nearReachableWalkway === true || poi.modelReachable === true
      : poi.insideDisplayPolygon === true) &&
    (category === "all" || (poi.categories ?? [poi.category]).includes(category)));
}

export function poiInfo(result) {
  return result?.poiInfo ?? result?.metadata?.poi ?? null;
}

export function poiCacheLabel(result) {
  const info = poiInfo(result);
  if (!info) return "待加载";
  if (info.status === "unavailable") return "检索不可用";
  if (info.stalePages) return "旧缓存 · 待更新";
  if (info.apiRequests > 0) return `API ${info.apiRequests} 次 · 缓存 ${info.cacheHits ?? 0} 项`;
  if (info.cacheHits > 0) return `缓存命中 ${info.cacheHits} 项`;
  return "百度 POI 候选";
}

export function poiSearchProgress(result) {
  const info = poiInfo(result);
  if (!info || !Number.isFinite(info.plannedQueries)) return "";
  const progress = `检索 ${info.completedQueries ?? 0}/${info.plannedQueries} 个网格关键词`;
  if (info.status === "ready") return `${progress} · 已完成当前检索计划，非设施普查`;
  if (info.quotaLimited) return `${progress} · 百度限流／配额限制。待限制恢复后再普通计算补查，缓存已保留；不要强制刷新。`;
  if (info.authFailed) return `${progress} · 百度鉴权／权限错误，请检查服务端 AK 配置；缓存已保留。`;
  const reason = info.requestBudgetReached ? "请求预算" : info.timeBudgetReached ? "时间预算" : "分页／数据限制";
  return `${progress} · 未完整：${reason}。再次普通计算可复用缓存补查，不必强制刷新。`;
}

// Count POI records, not unique institutions: a school's gates may have their
// own Baidu UIDs. Model reachability is deliberately separate from containment.
export function poiCategoryCounts(result) {
  return Object.entries(poiCategoryStyles).map(([category, style]) => {
    const summary = result?.poiCategories?.find(item => item.category === category);
    const displayed = visiblePois(result, category);
    return { category, label: style.label, color: style.color,
      queried: Number.isFinite(summary?.queriedCount) ? summary.queriedCount : null,
      displayed: displayed.length,
      modelReachable: displayed.filter(poi => poi.modelReachable === true).length };
  });
}

export function poiEmptyLabel(result, category = "all") {
  if (poiInfo(result)?.status === "unavailable") {
    return "未取得设施数据，不将空结果解释为没有设施。";
  }
  const row = poiCategoryCounts(result).find(item => item.category === category);
  const scope = result?.mode === "local_experiment" ? "可达街段附近" : "近似圈内";
  if (row) return `${scope}暂无${row.label}候选点位${row.queried !== null
    ? `（检索范围返回 ${row.queried} 个）` : ""}。不代表真实设施匮乏。`;
  return "当前范围未展示候选设施；请留意检索范围、分页上限和数据状态。";
}

export function poiAccessLabel(poi) {
  if (poi.modelReachable === true && Number.isFinite(poi.modelTravelTimeSeconds)) {
    return `路网模型约 ${(poi.modelTravelTimeSeconds / 60).toFixed(1)} 分钟 · 入口未核实`;
  }
  if (poi.modelReachable === false) return "路网模型不可达 · 非真实出行结论";
  return ({ missing_navigation_point: "无导航入口，尚未接入路网",
    not_on_modeled_way: "导航点不在已建模步行边上",
    ambiguous_side: "道路侧有歧义，尚未接入路网",
    coordinate_alignment_outside_grid: "点位超出已校准区域，尚未接入路网",
    coordinate_alignment_unavailable: "坐标校准未完成，尚未接入路网" })[poi.accessStatus] ?? "入口待核实";
}
