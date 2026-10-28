#pragma once

#include <cstddef>
#include <limits>

#include "isochrone/road_graph.hpp"
#include "isochrone/analysis_request.hpp"
#include "isochrone/execution_control.hpp"

namespace isochrone::detail {

struct Snap {
  std::size_t edge_index{};
  Point point;
  double distance_meters{std::numeric_limits<double>::infinity()};
  double surface_distance_meters{std::numeric_limits<double>::infinity()};
  double offset_meters{};
};

Snap project_to_edge(Point point, const WalkEdge& edge, std::size_t edge_index,
                     const ExecutionControl& control);
Snap snap_origin(const RoadGraph& graph, const AnalysisRequest& request,
                 const ExecutionControl& control);

}  // namespace isochrone::detail
