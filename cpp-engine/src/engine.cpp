#include "isochrone/engine.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <stdexcept>

#include "detail/computation_graph.hpp"
#include "detail/coverage_analysis.hpp"
#include "detail/display_geometry.hpp"
#include "detail/facility_analysis.hpp"
#include "detail/input_validation.hpp"
#include "detail/origin_snap.hpp"
#include "detail/path_geometry.hpp"
#include "detail/reachability.hpp"
#include "detail/shortest_path.hpp"

namespace isochrone {

namespace {
using detail::kEpsilon;
}  // namespace

void validate_graph(const EngineInput& input) {
  detail::validate_schema_version(input.schema_version);
  detail::validate_input(input.graph, input.request, ExecutionControl{});
}

EngineResult compute_reachability(const EngineInput& input) {
  detail::validate_schema_version(input.schema_version);
  return compute_reachability(input.graph, input.request);
}

EngineResult compute_reachability(const RoadGraph& graph,
                                  const AnalysisRequest& request) {
  return compute_reachability(graph, request, ExecutionControl{});
}

EngineResult compute_reachability(const RoadGraph& graph,
                                  const AnalysisRequest& request,
                                  const ExecutionControl& control) {
  control.Checkpoint();
  const auto started = std::chrono::steady_clock::now();
  auto checkpoint = started;
  EngineResult result;
  const auto record_stage = [&](const char* name) {
    control.Checkpoint();
    const auto now = std::chrono::steady_clock::now();
    result.stage_timings_ms.emplace_back(name,
        std::chrono::duration<double, std::milli>(now - checkpoint).count());
    checkpoint = now;
  };
  detail::validate_input(graph, request, control);
  if (request.facilities_only &&
      (request.local_experiment || request.route_facility_id || request.route_only)) {
    throw std::invalid_argument("facilitiesOnly cannot be combined with local or route requests");
  }
  if ((request.route_only && !request.route_facility_id) ||
      (request.route_facility_id && std::none_of(
          request.facilities.begin(), request.facilities.end(),
          [&](const auto& facility) {
            control.Poll();
            return facility.id == *request.route_facility_id;
          }))) {
    throw std::invalid_argument("routeFacilityId must identify an input facility");
  }
  record_stage("graphValidation");
  const detail::Snap snap = detail::snap_origin(graph, request, control);
  const double access_seconds = snap.distance_meters /
      request.walking_speed_meters_per_second;
  if (access_seconds >= request.threshold_seconds - kEpsilon) {
    throw OriginNotOnWalkway(
        "reaching the nearest walkway uses the entire time budget");
  }
  result.is_local_experiment = request.local_experiment.has_value();
  result.snapped_origin = snap.point;
  result.snap_distance_meters = snap.distance_meters;
  result.origin_access_seconds = access_seconds;
  if (request.allow_off_network_origin && snap.distance_meters > kEpsilon) {
    result.warnings.push_back("UNVERIFIED_STRAIGHT_LINE_ORIGIN_ACCESS");
  }
  if (snap.distance_meters > 10.0) {
    result.warnings.push_back("ORIGIN_SNAP_OVER_10_METERS");
  }
  record_stage("originSnap");

  const detail::ComputationGraph state =
      detail::build_computation_graph(graph, request, snap, control);
  const auto& graph_points = state.graph_points;
  const auto origin_index = state.origin_index;
  const auto& adjacency = state.adjacency;
  std::vector<std::size_t> previous_nodes, previous_edges;
  record_stage("graphBuild");
  const std::vector<double> arrival = detail::shortest_times(adjacency, {{
      origin_index, access_seconds}}, control,
      request.route_facility_id ? &previous_nodes : nullptr,
      request.route_facility_id ? &previous_edges : nullptr);
  record_stage("dijkstra");
  for (std::size_t i = 0; i < graph.nodes.size(); ++i) {
    control.Poll();
    if (arrival[i] <= request.threshold_seconds) ++result.reachable_node_count;
  }
  for (std::size_t i = 0; i < graph_points.size(); ++i) {
    control.Poll();
    if (std::abs(arrival[i] - request.threshold_seconds) <= kEpsilon) {
      result.frontier.push_back(graph_points[i]);
    }
  }

  detail::append_facility_results(graph, request, snap, access_seconds, state,
      arrival, previous_nodes, previous_edges, result, control);

  // POI clicks need only graph traversal and path recovery, not rasterization,
  // polygon buffering or per-category coverage analysis.
  record_stage("facilityTimes");
  if (request.route_only || request.facilities_only) {
    control.Checkpoint();
    return result;
  }

  const detail::ReachabilitySummary reachable =
      detail::append_reachable_edges(state, request, arrival, result, control);
  record_stage("reachableEdges");
  if (!request.local_experiment) {
    detail::RoadClosureFillStats closure_stats;
    result.display_polygons = detail::isochrone_polygons(
        result.reachable_edges, reachable.timed_edges, arrival, request.threshold_seconds,
        request.walking_speed_meters_per_second,
        request.display_area_radius_meters, request.display_buffer_meters,
        request.display_grid_step_meters,
        request.display_min_hole_area_square_meters, closure_stats, control);
    result.closed_road_face_count = closure_stats.face_count;
    result.road_closure_filled_cell_count = closure_stats.filled_cell_count;
    record_stage("displayGeometry");
  }
  detail::append_coverage_results(state, request, arrival,
      reachable.reachable_street_length, result, control);
  if (!request.local_experiment && result.frontier.empty()) {
    result.warnings.push_back(reachable.blocked_crossing
        ? "CROSSING_NOT_COMPLETED_WITHIN_THRESHOLD"
        : "NETWORK_MAY_END_BEFORE_TIME_LIMIT");
  }
  if (!request.local_experiment && result.display_polygons.empty()) {
    result.warnings.push_back("NO_DISPLAY_POLYGON");
  }
  record_stage("coverage");
  control.Checkpoint();
  return result;
}

}  // namespace isochrone
