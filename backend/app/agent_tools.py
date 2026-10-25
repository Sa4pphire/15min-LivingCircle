"""Read-only agent tools scoped to one authoritative, completed analysis.

The model receives small facility cards, never coordinates or graph geometry.
Route geometry stays on the application side for the existing map renderer.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
import math
from typing import Any

from .poi_categories import CATEGORIES


MAX_FACILITIES = 20
MAX_ROUTE_CANDIDATES = 8
ROUTE_BUDGET_SECONDS = 30.0
_CONTEXT_ERRORS = frozenset({
    "ROUTE_CONTEXT_EXPIRED", "CONTEXT_CHANGED", "ANALYSIS_NOT_FOUND",
    "ANALYSIS_NOT_READY", "REGION_MISMATCH", "AGENT_CONTEXT_CHANGED",
})
_CATEGORY_LABELS = {key: item["label"] for key, item in CATEGORIES.items()}
_CATEGORY_LABELS["healthcare"] = "医疗设施（含社区卫生服务中心）"


class ToolError(ValueError):
    """An application error with a safe public message, not provider details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _definition(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "name": name, "description": description,
            "strict": True, "parameters": {
                "type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}}


TOOL_DEFINITIONS = [
    _definition("get_analysis_summary",
                "读取当前已完成分析的范围、设施分类数量、数据局限和之前提到的设施。", {}),
    _definition("list_facilities",
                "筛选当前分析展示范围内的设施，最多返回20项。空清单不证明现实中没有设施。",
                {"category": {"type": "string", "enum": ["all", *CATEGORIES]},
                 "query": {"type": "string",
                           "description": "设施名称或地址关键词，最多120个字符；不过滤时传空字符串。"}}),
    _definition("find_nearest_medical_facility",
                "查询当前分析面内医疗设施（含社区卫生服务中心）的步行路线。"
                "最多探查8个候选、共30秒，按成功路线耗时选择，并明确未查完和未核实状态。", {}),
    _definition("get_facility_route",
                "按本次分析中真实存在的设施ID查询路线，返回距离耗时并请求地图显示。"
                "没有逐步转向指令；不得编造道路名、左转右转或入口开放情况。",
                {"poiId": {"type": "string",
                           "description": "从设施工具结果或之前设施引用中取得的非空ID，最多255个字符。"}}),
]


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _point(value: Any) -> tuple[float, float] | None:
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                    and math.isfinite(item) for item in value)):
        return float(value[0]), float(value[1])
    return None


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _error(code: str, message: str) -> dict:
    return {"status": "error", "error": {"code": code, "message": message}}


class AnalysisTools:
    """One chat turn's tools, route cache, cards and validated map action."""

    def __init__(self, report: dict, context: dict,
                 route_lookup: Callable[[str], Awaitable[dict]],
                 previous_refs: list[str] | None = None):
        self._report = report
        self._context = {key: context.get(key) for key in
                         ("regionId", "mode", "analysisId", "resultRevision")}
        self._route_lookup = route_lookup
        threshold = report.get("thresholdSeconds", 900)
        self._threshold = float(threshold) if _number(threshold) and threshold > 0 else 900.0
        self._records: dict[str, dict] = {}
        self._local_points: dict[str, tuple[float, float]] = {}
        self._bd09_points: dict[str, tuple[float, float]] = {}
        self._routes: dict[str, dict] = {}
        self._route_errors: dict[str, dict] = {}
        self._cards: OrderedDict[str, dict] = OrderedDict()
        self.facilities: list[dict] = []
        self.map_action: dict | None = None
        self.comparison: dict | None = None
        self._read_facilities(report)
        self.references = []
        for identity in previous_refs or []:
            if isinstance(identity, str) and identity in self._records:
                if identity in self.references:
                    self.references.remove(identity)
                self.references.append(identity)
        self.references = self.references[-MAX_FACILITIES:]

    def _read_facilities(self, report: dict) -> None:
        collection = report.get("poiFacilities")
        if not isinstance(collection, dict):
            return
        features = collection.get("features")
        if not isinstance(features, list):
            return
        for feature in features:
            if not isinstance(feature, dict):
                continue
            data = feature.get("properties")
            if not isinstance(data, dict) or data.get("insideDisplayPolygon") is not True:
                continue
            identity = data.get("id")
            if (not isinstance(identity, str) or not identity or len(identity) > 255
                    or identity in self._records or data.get("category") not in CATEGORIES):
                continue
            categories = data.get("categories")
            categories = [key for key in CATEGORIES if isinstance(categories, list) and key in categories]
            if data["category"] not in categories:
                categories.append(data["category"])
            self._records[identity] = {**data, "categories": categories}
            local = _point(data.get("localPointMeters"))
            if local is not None:
                self._local_points[identity] = local
            geometry = feature.get("geometry")
            if (isinstance(geometry, dict) and geometry.get("type") == "Point"
                    and collection.get("coordType") == "bd09ll"):
                geographic = _point(geometry.get("coordinates"))
                if geographic and abs(geographic[0]) <= 180 and abs(geographic[1]) < 90:
                    self._bd09_points[identity] = geographic

    def _info(self) -> dict:
        info = self._report.get("poiInfo")
        metadata = self._report.get("metadata")
        if not isinstance(info, dict) and isinstance(metadata, dict):
            info = metadata.get("poi")
        return info if isinstance(info, dict) else {}

    def _card(self, identity: str) -> dict:
        data = self._records[identity]
        name, tag = _text(data.get("name"), 160), _text(data.get("tag"), 160)
        if "healthcare" in data["categories"]:
            if "社区卫生服务中心" in name or "社区卫生服务中心" in tag:
                facility_type = "社区卫生服务中心"
            elif "医院" in name:
                facility_type = "医院"
            else:
                facility_type = "医疗设施"
        else:
            facility_type = _CATEGORY_LABELS[data["category"]]
        card = {"id": identity, "name": name or "未命名设施",
                "address": _text(data.get("address"), 240),
                "facilityType": facility_type, "category": data["category"]}
        route = self._routes.get(identity)
        if route is not None:
            warnings = route.get("warnings")
            warnings = [_text(item, 160) for item in warnings if isinstance(item, str)][:8] \
                if isinstance(warnings, list) else []
            if route.get("accessVerified") is not True and "ACCESS_UNVERIFIED" not in warnings:
                warnings.append("ACCESS_UNVERIFIED")
            card.update(routeStatus="ready", lengthMeters=route["lengthMeters"],
                        travelTimeSeconds=route["travelTimeSeconds"],
                        withinThreshold=route["travelTimeSeconds"] <= self._threshold,
                        accessVerified=route.get("accessVerified") is True, warnings=warnings)
        return card

    def _publish(self, identity: str) -> dict:
        card = self._card(identity)
        self._cards[identity] = card
        self._cards.move_to_end(identity)
        while len(self._cards) > MAX_FACILITIES:
            self._cards.popitem(last=False)
        self.facilities = list(self._cards.values())
        if identity in self.references:
            self.references.remove(identity)
        self.references.append(identity)
        self.references = self.references[-MAX_FACILITIES:]
        return card

    async def execute(self, name: str, arguments: dict) -> dict:
        required = {"get_analysis_summary": set(), "list_facilities": {"category", "query"},
                    "find_nearest_medical_facility": set(), "get_facility_route": {"poiId"}}
        if not isinstance(name, str) or name not in required:
            return _error("UNKNOWN_TOOL", "请求的工具不可用。")
        if not isinstance(arguments, dict) or set(arguments) != required[name]:
            return _error("INVALID_TOOL_ARGUMENTS", "工具参数格式不正确，请按工具定义提供参数。")
        if name == "get_analysis_summary":
            return self._summary()
        if name == "list_facilities":
            return self._list(arguments)
        if name == "find_nearest_medical_facility":
            return await self._nearest_medical()
        identity = arguments.get("poiId")
        if not isinstance(identity, str) or not identity or len(identity) > 255:
            return _error("INVALID_POI_ID", "请使用当前设施清单中的有效ID。")
        if identity not in self._records:
            return _error("POI_NOT_IN_ANALYSIS", "该设施不在当前分析的展示候选清单中。")
        self.map_action = None
        if self.comparison and self.comparison.get("selectedPoiId") != identity:
            self.comparison = None
        result = await self._route(identity)
        if result.get("status") == "ready":
            self.map_action = {"type": "show_route", "poiId": identity,
                               "route": self._routes[identity]}
        return result

    def _summary(self) -> dict:
        counts = {key: sum(key in item["categories"] for item in self._records.values())
                  for key in CATEGORIES}
        info = self._info()
        return {"status": "ready", "context": self._context,
                "thresholdSeconds": self._threshold,
                "candidateScope": "current_analysis_inside_display_polygon",
                "candidateCount": len(self._records), "categoryCounts": counts,
                "categoryLabels": _CATEGORY_LABELS,
                "poiStatus": info.get("status") if info.get("status") in
                ("ready", "partial", "pending", "unavailable", "error") else "unknown",
                "inventoryVerified": info.get("inventoryVerified") is True,
                "accessVerified": False,
                "references": [self._card(identity) for identity in self.references],
                "message": "数量仅表示本次分析展示范围内的已知候选，不能据此判断现实中设施是否缺失。"
                           "医疗类别包含社区卫生服务中心；面内地点不等于已验证15分钟可达。"}

    def _list(self, arguments: dict) -> dict:
        category, query = arguments["category"], arguments["query"]
        if (not isinstance(category, str) or category not in ("all", *CATEGORIES)
                or not isinstance(query, str) or len(query) > 120):
            return _error("INVALID_TOOL_ARGUMENTS", "请选择已有设施类别，关键词不超过120个字符。")
        query = query.strip().casefold()
        identities = [identity for identity, data in self._records.items()
                      if (category == "all" or category in data["categories"])
                      and (not query or query in (str(data.get("name", "")) + " "
                                                 + str(data.get("address", ""))).casefold())]
        identities.sort(key=lambda identity: (_text(self._records[identity].get("name"), 160), identity))
        cards = [self._publish(identity) for identity in identities[:MAX_FACILITIES]]
        return {"status": "ready" if cards else "empty", "facilities": cards,
                "totalMatches": len(identities), "truncated": len(identities) > len(cards),
                "candidateScope": "current_analysis_inside_display_polygon",
                "message": "仅列出当前分析内的已知候选，清单可能不完整；医疗类别含社区卫生服务中心。"
                if cards else "当前分析候选中没有匹配记录，不能据此断言附近没有此类设施。"}

    async def _route(self, identity: str) -> dict:
        if identity in self._routes:
            return {"status": "ready", "facility": self._publish(identity),
                    "message": "已显示地图路线；当前数据没有逐步转向指令，入口和现实通行情况未经核实。"}
        if identity in self._route_errors:
            return self._route_errors[identity]
        try:
            route = await self._route_lookup(identity)
        except Exception as exc:
            code = getattr(exc, "code", "ROUTE_UNAVAILABLE")
            if code in _CONTEXT_ERRORS:
                raise ToolError(code, "当前分析上下文已变化或失效，请重新计算后再查询。") from exc
            if isinstance(exc, ToolError):
                result = _error(exc.code, exc.message)
            elif code == "ENGINE_BUSY":
                result = _error(code, "路线计算资源繁忙，请稍后重试。")
            elif isinstance(exc, TimeoutError):
                result = _error("ROUTE_TIMEOUT", "路线查询超时，请稍后重试。")
            else:
                result = _error("ROUTE_UNAVAILABLE", "这条路线暂时无法查询，请稍后重试。")
            self._route_errors[identity] = result
            return result
        if (not isinstance(route, dict) or route.get("analysisId") != self._context["analysisId"]
                or route.get("poiId") != identity):
            raise ToolError("CONTEXT_CHANGED", "路线与当前分析不一致，请重新计算后再查询。")
        if route.get("status") not in ("ready", "unmapped", "unreachable"):
            result = _error("INVALID_ROUTE_RESULT", "路线返回结果暂时不可用。")
        elif route["status"] != "ready":
            result = {"status": route["status"], "facility": self._publish(identity),
                      "message": "该候选在当前路网中没有可用路线；这不代表现实中一定无法到达。"}
        elif not _number(route.get("lengthMeters")) or not _number(route.get("travelTimeSeconds")):
            result = _error("INVALID_ROUTE_RESULT", "路线距离或耗时不可用，未生成估算回答。")
        else:
            self._routes[identity] = route
            return {"status": "ready", "facility": self._publish(identity),
                    "message": "已显示地图路线；当前数据没有逐步转向指令，入口和现实通行情况未经核实。"}
        self._route_errors[identity] = result
        return result

    def _distance_key(self, identity: str) -> tuple[float, str]:
        origin = _point(self._report.get("_agentOriginMeters"))
        local = self._local_points.get(identity)
        if origin is not None and local is not None:
            return math.dist(origin, local), identity
        center = self._report.get("analysisCenter")
        geographic = self._bd09_points.get(identity)
        if (isinstance(center, dict) and center.get("coordType") == "bd09ll"
                and geographic is not None):
            origin = _point([center.get("lng"), center.get("lat")])
            if origin is not None and abs(origin[0]) <= 180 and abs(origin[1]) < 90:
                dx = (geographic[0] - origin[0]) * math.cos(math.radians(origin[1]))
                return math.hypot(dx, geographic[1] - origin[1]), identity
        return math.inf, identity

    async def _nearest_medical(self) -> dict:
        self.map_action = None
        self.comparison = None
        candidates = sorted((identity for identity, data in self._records.items()
                             if "healthcare" in data["categories"]), key=self._distance_key)
        checked, succeeded, available = 0, 0, []
        deadline = asyncio.get_running_loop().time() + ROUTE_BUDGET_SECONDS
        for identity in candidates[:MAX_ROUTE_CANDIDATES]:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            checked += 1
            try:
                result = await asyncio.wait_for(self._route(identity), timeout=remaining)
            except TimeoutError:
                self._route_errors[identity] = _error("ROUTE_TIMEOUT", "本轮路线查询时间预算已用完。")
                break
            if result.get("status") == "ready":
                succeeded += 1
                available.append(identity)
        selected = min(available, key=lambda identity: (
            self._routes[identity]["travelTimeSeconds"] > self._threshold,
            self._routes[identity]["travelTimeSeconds"],
            self._routes[identity]["lengthMeters"], identity), default=None)
        partial = checked < len(candidates) or succeeded < checked
        status = ("empty" if not candidates else "no_route" if selected is None
                  else "partial" if partial else "complete")
        self.comparison = {"scope": "current_analysis_inside_display_polygon",
                           "metric": "walking_time", "status": status, "partial": partial,
                           "totalCandidates": len(candidates), "checkedCandidates": checked,
                           "successfulCandidates": succeeded, "failed": checked - succeeded,
                           "unqueried": len(candidates) - checked,
                           "selectedPoiId": selected,
                           "withinThreshold": (self._routes[selected]["travelTimeSeconds"] <= self._threshold)
                           if selected else None,
                           "inventoryVerified": self._info().get("inventoryVerified") is True,
                           "candidateLimit": MAX_ROUTE_CANDIDATES,
                           "timeBudgetSeconds": ROUTE_BUDGET_SECONDS}
        if selected is None:
            message = ("当前分析的候选清单中没有医疗设施记录，不能据此断言附近没有医院。"
                       if not candidates else
                       f"当前分析有{len(candidates)}个医疗设施候选，已探查{checked}个，"
                       "暂未取得可用路线；未查询和查询失败的设施仍可能可达。")
            return {"status": status, "comparison": self.comparison, "message": message}
        card = self._publish(selected)
        self.map_action = {"type": "show_route", "poiId": selected, "route": self._routes[selected]}
        minutes = math.ceil(card["travelTimeSeconds"] / 60)
        walking_time = "不到1分钟" if card["travelTimeSeconds"] < 60 else f"约{minutes}分钟"
        message = (f"在本次分析范围内已成功查询的{succeeded}个医疗设施候选中，"
                   f"{card['name']}（{card['facilityType']}）步行耗时最短，"
                   f"{walking_time}、{round(card['lengthMeters'])}米，已显示地图路线。")
        if not card["withinThreshold"]:
            message += f"这条路线超过本次{self._threshold / 60:g}分钟预算。"
        if partial:
            message += f"共{len(candidates)}个候选，已探查{checked}个；存在失败或未查询项，不能确定整体最近。"
        message += "医疗类别包含社区卫生服务中心，设施清单和入口均未经完整核实。"
        return {"status": status, "facility": card, "comparison": self.comparison,
                "message": message}
