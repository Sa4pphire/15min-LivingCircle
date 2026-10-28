#include "detail/coverage_analysis.hpp"

#include <string>
#include <utility>

#include "detail/display_geometry.hpp"
#include "detail/path_geometry.hpp"
#include "detail/reachability.hpp"
#include "detail/shortest_path.hpp"

namespace isochrone::detail {

void append_coverage_results(
    const ComputationGraph& state, const AnalysisRequest& request,
    const std::vector<double>& arrival, double reachable_street_length,
    EngineResult& result, const ExecutionControl& control) {
  const auto& node_indices = state.node_indices;
  const auto& bindings = state.bindings;
  const auto& edges = state.edges;
  const auto& adjacency = state.adjacency;
  if (!request.local_experiment) {
    for (const ServiceCategory& category : request.service_categories) {
      control.Poll();
      GrayZone zone;
      zone.category = category.id;
      zone.status = category.data_status == "reviewed_online"
          ? "candidate" : "data_insufficient";
      zone.reachable_length_meters = reachable_street_length;
      if (category.data_status == "reviewed_online") {
        std::vector<std::pair<std::size_t, double>> sources;
        for (const EntranceBinding& binding : bindings) {
          control.Poll();
          if (request.facilities[binding.facility_index].category == category.id) {
            sources.emplace_back(binding.node, binding.extra_seconds);
          }
        }
        const std::vector<double> service_times = shortest_times(adjacency, sources, control);
        for (const InternalEdge& edge : edges) {
          control.Poll();
          if (edge.kind != EdgeKind::walkway) continue;
          const auto from_origin = reachable_intervals(
              edge, arrival, request.threshold_seconds,
              request.walking_speed_meters_per_second);
          const auto from_facilities = reachable_intervals(
              edge, service_times, request.threshold_seconds,
              request.walking_speed_meters_per_second);
          for (const Interval part : subtract_intervals(
                   from_origin, from_facilities, control)) {
            control.Poll();
            zone.uncovered_edges.push_back({edge.source_id, edge.kind,
                slice(edge.path, part.from, part.to, control), edge.width_meters});
            zone.uncovered_length_meters += part.to - part.from;
          }
        }
        zone.display_polygons = display_polygons(
            zone.uncovered_edges, request.display_buffer_meters,
            request.display_grid_step_meters, control);
      }
      result.gray_zones.push_back(std::move(zone));
    }
  } else {
    std::vector<std::pair<std::size_t, double>> boundary_sources;
    bool reaches_cut_boundary = false;
    for (const auto& id : request.local_experiment->boundary_node_ids) {
      control.Poll();
      const std::size_t node = node_indices.at(id);
      boundary_sources.emplace_back(node, 0.0);
      if (arrival[node] <= request.threshold_seconds + kEpsilon) {
        reaches_cut_boundary = true;
      }
    }
    if (reaches_cut_boundary) {
      result.warnings.push_back("LOCAL_REACHABILITY_MAY_BE_TRUNCATED");
    }
    const std::vector<double> boundary_times =
        shortest_times(adjacency, boundary_sources, control);
    const bool topology_verified =
        request.local_experiment->topology_status == "verified";
    if (!topology_verified) {
      result.warnings.push_back("LOCAL_TOPOLOGY_UNVERIFIED");
    }

    for (const ServiceCategory& category : request.service_categories) {
      control.Poll();
      LocalGrayZone zone;
      zone.category = category.id;
      const bool inventory_verified =
          category.local_inventory_status == "verified";
      if (!topology_verified) {
        zone.warnings.push_back("LOCAL_TOPOLOGY_UNVERIFIED");
      }
      if (!inventory_verified) {
        zone.warnings.push_back("LOCAL_FACILITY_INVENTORY_INCOMPLETE");
      }
      std::vector<std::pair<std::size_t, double>> sources;
      for (const EntranceBinding& binding : bindings) {
        control.Poll();
        if (request.facilities[binding.facility_index].category == category.id) {
          sources.emplace_back(binding.node, binding.extra_seconds);
        }
      }
      const std::vector<double> service_times = shortest_times(adjacency, sources, control);
      for (const InternalEdge& edge : edges) {
        control.Poll();
        if (edge.kind != EdgeKind::walkway) continue;
        const auto from_origin = reachable_intervals(
            edge, arrival, request.threshold_seconds,
            request.walking_speed_meters_per_second);
        const auto from_facilities = reachable_intervals(
            edge, service_times, request.threshold_seconds,
            request.walking_speed_meters_per_second);
        const auto append_parts = [&](const std::vector<Interval>& parts,
                                      std::vector<ReachableEdge>& output,
                                      double& length) {
          for (const Interval part : parts) {
            control.Poll();
            output.push_back({edge.source_id, edge.kind,
                slice(edge.path, part.from, part.to, control), edge.width_meters});
            length += part.to - part.from;
          }
        };
        append_parts(intersect_intervals(from_origin, from_facilities, control),
                     zone.covered_edges, zone.covered_length_meters);
        const auto unserved = subtract_intervals(from_origin, from_facilities, control);
        if (!topology_verified || !inventory_verified) {
          append_parts(unserved, zone.unknown_edges,
                       zone.unknown_length_meters);
          continue;
        }
        const auto from_boundary = reachable_intervals(
            edge, boundary_times, request.threshold_seconds,
            request.walking_speed_meters_per_second);
        append_parts(intersect_intervals(unserved, from_boundary, control),
                     zone.unknown_edges, zone.unknown_length_meters);
        append_parts(subtract_intervals(unserved, from_boundary, control),
                     zone.candidate_uncovered_edges,
                     zone.candidate_uncovered_length_meters);
      }
      result.local_gray_zones.push_back(std::move(zone));
    }
  }
}

}  // namespace isochrone::detail
