#pragma once

#include <cstddef>
#include <vector>

#include "isochrone/analysis_result.hpp"
#include "computation_graph.hpp"

namespace isochrone::detail {

struct TimedDisplayEdge {
  const InternalEdge* edge;
  Interval interval;
};

struct RoadClosureFillStats {
  std::size_t face_count{};
  std::size_t filled_cell_count{};
};

std::vector<DisplayPolygon> display_polygons(
    const std::vector<ReachableEdge>& reachable, double buffer,
    double grid_step, const ExecutionControl& control);
std::vector<DisplayPolygon> isochrone_polygons(
    const std::vector<ReachableEdge>& reachable,
    const std::vector<TimedDisplayEdge>& timed_edges,
    const std::vector<double>& arrival, double threshold, double speed,
    double area_radius, double connector_radius, double grid_step,
    double min_hole_area, RoadClosureFillStats& closure_stats,
    const ExecutionControl& control);

}  // namespace isochrone::detail
