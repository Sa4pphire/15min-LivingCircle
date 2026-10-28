#include "detail/facility_analysis.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

#include "detail/path_geometry.hpp"

namespace isochrone::detail {

void append_facility_results(
    const RoadGraph& graph, const AnalysisRequest& request, const Snap& snap,
    double access_seconds, const ComputationGraph& state,
    const std::vector<double>& arrival,
    const std::vector<std::size_t>& previous_nodes,
    const std::vector<std::size_t>& previous_edges,
    EngineResult& result, const ExecutionControl& control) {
  const auto& graph_points = state.graph_points;
  const auto origin_index = state.origin_index;
  const auto& bindings = state.bindings;
  const auto& edges = state.edges;
  for (std::size_t i = 0; i < request.facilities.size(); ++i) {
    control.Poll();
    const FacilityAccess& facility = request.facilities[i];
    double best_time = std::numeric_limits<double>::infinity();
    std::string best_edge;
    std::optional<std::string> best_entrance;
    const EntranceBinding* best_binding = nullptr;
    for (const EntranceBinding& binding : bindings) {
      control.Poll();
      if (binding.facility_index != i) continue;
      if (best_edge.empty()) best_edge = binding.access_edge_id;
      double candidate = arrival[binding.node] + binding.extra_seconds;
      if (!binding.has_path && binding.access_edge_id ==
              graph.edges[snap.edge_index].id &&
          distance(binding.street_point, request.origin) <= kEpsilon) {
        candidate = 0.0;
      }
      if (candidate < best_time) {
        best_time = candidate;
        best_edge = binding.access_edge_id;
        best_entrance = binding.id;
        best_binding = &binding;
      }
    }
    result.facility_travel_times.push_back({facility.id, best_edge,
        std::isfinite(best_time) ? std::optional<double>{best_time} : std::nullopt,
        best_time <= request.threshold_seconds + kEpsilon,
        facility.category, best_entrance});
    if (request.route_facility_id && facility.id == *request.route_facility_id) {
      FacilityRoute route;
      route.facility_id = facility.id;
      route.entrance_id = best_entrance;
      route.connected = std::isfinite(best_time);
      route.within_threshold = best_time <= request.threshold_seconds + kEpsilon;
      if (route.connected) {
        route.travel_time_seconds = best_time;
        const auto append = [&](std::string id, std::string kind,
                                std::vector<Point> path, double seconds) {
          if (path.size() < 2 || path_length(path, control) <= kEpsilon) return;
          route.length_meters += path_length(path, control);
          for (const Point point : path) {
            control.Poll();
            if (route.path.empty() || distance(route.path.back(), point) > kEpsilon) {
              route.path.push_back(point);
            }
          }
          route.segments.push_back({std::move(id), std::move(kind), std::move(path), seconds});
        };
        if (best_time > kEpsilon) {
          append("", "origin_access", {request.origin, snap.point}, access_seconds);
          std::vector<std::pair<std::size_t, std::size_t>> traversed;
          std::size_t node = best_binding->node;
          while (node != origin_index) {
            control.Poll();
            if (traversed.size() >= graph_points.size() || previous_nodes[node] >= graph_points.size()) {
              throw std::runtime_error("invalid shortest-path predecessor chain");
            }
            traversed.push_back({previous_edges[node], previous_nodes[node]});
            node = previous_nodes[node];
          }
          control.Checkpoint();
          std::reverse(traversed.begin(), traversed.end());
          control.Checkpoint();
          for (const auto& [index, from] : traversed) {
            control.Poll();
            const auto& edge = edges.at(index);
            auto path = edge.path;
            if (from == edge.to) {
              control.Checkpoint();
              std::reverse(path.begin(), path.end());
              control.Checkpoint();
            }
            route.crossing_wait_seconds += edge.wait_seconds;
            append(edge.source_id, edge_kind_name(edge.kind), std::move(path),
                   edge.length / request.walking_speed_meters_per_second + edge.wait_seconds);
          }
          append("", "facility_access", {graph_points[best_binding->node], best_binding->street_point},
                 distance(graph_points[best_binding->node], best_binding->street_point) /
                     request.walking_speed_meters_per_second);
          append("", "facility_access", best_binding->access_path,
                 path_length(best_binding->access_path, control) / request.walking_speed_meters_per_second);
        }
        // A zero-distance route is still a valid LineString in downstream GeoJSON.
        if (route.path.empty()) route.path = {request.origin, request.origin};
      }
      result.facility_route = std::move(route);
    }
  }

}

}  // namespace isochrone::detail
