#include "detail/reachability.hpp"

#include <algorithm>
#include <cmath>

#include "detail/path_geometry.hpp"

namespace isochrone::detail {

std::vector<Interval> reachable_intervals(
    const InternalEdge& edge, const std::vector<double>& times,
    double threshold, double speed) {
  if (edge.kind == EdgeKind::crossing) {
    const double cost = edge.length / speed + edge.wait_seconds;
    if (times[edge.from] + cost <= threshold + kEpsilon ||
        times[edge.to] + cost <= threshold + kEpsilon) {
      return {{0.0, edge.length}};
    }
    return {};
  }
  const auto reach = [&](double time) {
    return std::isfinite(time)
        ? std::clamp((threshold - time) * speed, 0.0, edge.length)
        : 0.0;
  };
  const double front = reach(times[edge.from]);
  const double back = reach(times[edge.to]);
  if (front + back >= edge.length - kEpsilon) {
    return front > kEpsilon || back > kEpsilon
        ? std::vector<Interval>{{0.0, edge.length}}
        : std::vector<Interval>{};
  }
  std::vector<Interval> intervals;
  if (front > kEpsilon) intervals.push_back({0.0, front});
  if (back > kEpsilon) intervals.push_back({edge.length - back, edge.length});
  return intervals;
}

std::vector<Interval> subtract_intervals(
    const std::vector<Interval>& base,
    const std::vector<Interval>& covered, const ExecutionControl& control) {
  std::vector<Interval> result;
  for (const Interval piece : base) {
    control.Poll();
    double cursor = piece.from;
    for (const Interval service : covered) {
      control.Poll();
      if (service.to <= cursor + kEpsilon || service.from >= piece.to - kEpsilon) {
        continue;
      }
      if (service.from > cursor + kEpsilon) {
        result.push_back({cursor, std::min(service.from, piece.to)});
      }
      cursor = std::max(cursor, service.to);
      if (cursor >= piece.to - kEpsilon) break;
    }
    if (cursor < piece.to - kEpsilon) result.push_back({cursor, piece.to});
  }
  return result;
}

std::vector<Interval> intersect_intervals(
    const std::vector<Interval>& first,
    const std::vector<Interval>& second, const ExecutionControl& control) {
  std::vector<Interval> result;
  for (const Interval a : first) {
    control.Poll();
    for (const Interval b : second) {
      control.Poll();
      const double from = std::max(a.from, b.from);
      const double to = std::min(a.to, b.to);
      if (to > from + kEpsilon) result.push_back({from, to});
    }
  }
  return result;
}

ReachabilitySummary append_reachable_edges(
    const ComputationGraph& state, const AnalysisRequest& request,
    const std::vector<double>& arrival, EngineResult& result,
    const ExecutionControl& control) {
  ReachabilitySummary summary;
  auto& reachable_street_length = summary.reachable_street_length;
  auto& blocked_crossing = summary.blocked_crossing;
  auto& timed_edges = summary.timed_edges;
  const auto& edges = state.edges;
  for (const auto& edge : edges) {
    control.Poll();
    const auto intervals = reachable_intervals(
        edge, arrival, request.threshold_seconds,
        request.walking_speed_meters_per_second);
    if (edge.kind == EdgeKind::crossing) {
      if (!intervals.empty()) ++result.reachable_crossing_count;
      else if (arrival[edge.from] <= request.threshold_seconds + kEpsilon ||
               arrival[edge.to] <= request.threshold_seconds + kEpsilon) {
        blocked_crossing = true;
      }
    }
    for (const Interval part : intervals) {
      control.Poll();
      timed_edges.push_back({&edge, part});
      result.reachable_edges.push_back({edge.source_id, edge.kind,
          slice(edge.path, part.from, part.to, control), edge.width_meters});
      if (part.from > kEpsilon) {
        result.frontier.push_back(point_at(edge.path, part.from, control));
      }
      if (part.to < edge.length - kEpsilon) {
        result.frontier.push_back(point_at(edge.path, part.to, control));
      }
      if (edge.kind == EdgeKind::walkway) {
        reachable_street_length += part.to - part.from;
      }
    }
  }
  return summary;
}

}  // namespace isochrone::detail
