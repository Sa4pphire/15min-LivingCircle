#include "detail/origin_snap.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

#include "detail/path_geometry.hpp"
#include "isochrone/engine_errors.hpp"

namespace isochrone::detail {

Snap project_to_edge(Point point, const WalkEdge& edge,
                     std::size_t edge_index,
                     const ExecutionControl& control) {
  Snap best;
  best.edge_index = edge_index;
  double walked = 0.0;
  for (std::size_t i = 1; i < edge.path.size(); ++i) {
    control.Poll();
    const Point a = edge.path[i - 1];
    const Point b = edge.path[i];
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double squared_length = dx * dx + dy * dy;
    const double fraction = squared_length > 0.0
                                ? std::clamp(((point.x - a.x) * dx +
                                              (point.y - a.y) * dy) /
                                                 squared_length,
                                             0.0, 1.0)
                                : 0.0;
    const Point projection = interpolate(a, b, fraction);
    const double candidate_distance = distance(point, projection);
    if (candidate_distance < best.distance_meters) {
      best.point = projection;
      best.distance_meters = candidate_distance;
      best.surface_distance_meters = is_shared_walkway(edge)
          ? std::max(0.0, candidate_distance - edge.width_meters / 2.0)
          : candidate_distance;
      best.offset_meters = walked + fraction * std::sqrt(squared_length);
    }
    walked += std::sqrt(squared_length);
  }
  return best;
}

namespace {

bool share_endpoint(const WalkEdge& first, const WalkEdge& second) {
  return first.from == second.from || first.from == second.to ||
         first.to == second.from || first.to == second.to;
}

}  // namespace

Snap snap_origin(const RoadGraph& graph, const AnalysisRequest& request,
                 const ExecutionControl& control) {
  std::vector<Snap> candidates;
  for (std::size_t edge_index = 0; edge_index < graph.edges.size();
       ++edge_index) {
    control.Poll();
    const auto& edge = graph.edges[edge_index];
    if (edge.kind == EdgeKind::walkway) {
      candidates.push_back(project_to_edge(request.origin, edge, edge_index, control));
    }
  }
  if (candidates.empty()) {
    throw OriginNotOnWalkway("walking graph has no traversable street edge");
  }
  const auto in_range = [&](const Snap& candidate) {
    const WalkEdge& edge = graph.edges[candidate.edge_index];
    if (request.allow_off_network_origin) {
      return candidate.distance_meters <= request.max_origin_snap_meters;
    }
    return candidate.surface_distance_meters <=
        (is_shared_walkway(edge) ? 3.0 : request.max_origin_snap_meters);
  };
  const auto access_distance = [&](const Snap& candidate) {
    return request.allow_off_network_origin ? candidate.distance_meters
                                            : candidate.surface_distance_meters;
  };
  auto nearest = candidates.end();
  for (auto candidate = candidates.begin(); candidate != candidates.end();
       ++candidate) {
    control.Poll();
    if (in_range(*candidate) &&
        (nearest == candidates.end() || access_distance(*candidate) <
                                           access_distance(*nearest))) {
      nearest = candidate;
    }
  }
  if (nearest == candidates.end()) {
    throw OriginNotOnWalkway("origin is outside the supported walkway snap range");
  }
  if (request.origin_edge_id) {
    const auto selected = std::find_if(
        candidates.begin(), candidates.end(), [&](const Snap& snap) {
          control.Poll();
          return graph.edges[snap.edge_index].id == *request.origin_edge_id;
        });
    if (selected == candidates.end()) {
      throw std::invalid_argument("originEdgeId must identify a walkway");
    }
    if (!in_range(*selected) ||
        access_distance(*selected) >
            access_distance(*nearest) + kOriginSideAmbiguityMeters) {
      throw OriginNotOnWalkway(
          "originEdgeId is not on the nearest sidewalk side");
    }
    return *selected;
  }
  for (const Snap& candidate : candidates) {
    control.Poll();
    if (candidate.edge_index != nearest->edge_index &&
        !share_endpoint(graph.edges[candidate.edge_index],
                        graph.edges[nearest->edge_index]) &&
        in_range(candidate) && access_distance(candidate) <=
            access_distance(*nearest) + kOriginSideAmbiguityMeters) {
      throw AmbiguousOriginSide(
          "multiple sidewalk sides are equally near: " +
          graph.edges[nearest->edge_index].id + ", " +
          graph.edges[candidate.edge_index].id +
          "; specify originEdgeId");
    }
  }
  return *nearest;
}

}  // namespace isochrone::detail
