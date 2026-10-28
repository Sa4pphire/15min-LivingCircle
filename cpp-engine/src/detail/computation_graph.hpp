#pragma once

#include <cstddef>
#include <string>
#include <unordered_map>
#include <vector>

#include "isochrone/road_graph.hpp"
#include "isochrone/analysis_request.hpp"
#include "isochrone/execution_control.hpp"
#include "origin_snap.hpp"

namespace isochrone::detail {

struct InternalEdge {
  std::string source_id;
  EdgeKind kind;
  std::size_t from{};
  std::size_t to{};
  std::vector<Point> path;
  double length{};
  double start_offset{};
  double width_meters{};
  double wait_seconds{};
};

struct Interval {
  double from{};
  double to{};
};

struct Arc {
  std::size_t to{};
  double cost{};
  std::size_t edge_index{};
};

struct EntranceBinding {
  std::size_t facility_index;
  std::string id;
  std::string access_edge_id;
  Point street_point;
  bool has_path;
  std::size_t node;
  double extra_seconds;
  std::vector<Point> access_path;
};

// Owns only this call's split graph and entrance bindings. No member aliases
// the base graph. Keep this object alive and unchanged while display edges
// borrow pointers into its edges vector.
struct ComputationGraph {
  std::unordered_map<std::string, std::size_t> node_indices;
  std::vector<Point> graph_points;
  std::size_t origin_index{};
  std::vector<EntranceBinding> bindings;
  std::vector<InternalEdge> edges;
  std::vector<std::vector<Arc>> adjacency;
};

ComputationGraph build_computation_graph(
    const RoadGraph& graph, const AnalysisRequest& request, const Snap& snap,
    const ExecutionControl& control);

}  // namespace isochrone::detail
