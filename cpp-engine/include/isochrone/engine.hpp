#pragma once

#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

enum class EdgeKind { sidewalk, turn, crossing };

class OriginNotOnWalkway : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

struct WalkNode {
  std::string id;
  Point point;
};

struct WalkEdge {
  std::string id;
  std::string from;
  std::string to;
  EdgeKind kind{EdgeKind::sidewalk};
  std::vector<Point> path;
  std::string street_block_id;
  std::string side;
};

struct EngineInput {
  int schema_version{2};
  Point origin;
  std::optional<std::string> origin_edge_id;
  double threshold_seconds{900.0};
  double walking_speed_meters_per_second{1.3};
  double crossing_wait_seconds{20.0};
  double display_buffer_meters{15.0};
  double display_grid_step_meters{10.0};
  std::vector<WalkNode> nodes;
  std::vector<WalkEdge> edges;
};

struct ReachableEdge {
  std::string edge_id;
  EdgeKind kind;
  std::vector<Point> path;
};

struct EngineResult {
  Point snapped_origin;
  double snap_distance_meters{};
  std::vector<ReachableEdge> reachable_edges;
  std::vector<Point> frontier;
  std::vector<Ring> display_polygons;
  std::size_t reachable_node_count{};
  std::size_t reachable_crossing_count{};
  std::vector<std::string> warnings;
};

[[nodiscard]] const char* edge_kind_name(EdgeKind kind);
void validate_graph(const EngineInput& input);
[[nodiscard]] EngineResult compute_reachability(const EngineInput& input);

}  // namespace isochrone
