#pragma once

#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

enum class EdgeKind { sidewalk, shared_way, turn, crossing };

class OriginNotOnWalkway : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

class AmbiguousOriginSide : public std::runtime_error {
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
  std::string shared_way_type;
  double width_meters{};
};

struct FacilityAccess {
  std::string id;
  std::string access_edge_id;
  Point access_point;
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
  std::vector<FacilityAccess> facilities;
};

struct ReachableEdge {
  std::string edge_id;
  EdgeKind kind;
  std::vector<Point> path;
  double width_meters{};
};

struct DisplayPolygon {
  Ring outer;
  std::vector<Ring> holes;
};

struct FacilityTravelTime {
  std::string id;
  std::string access_edge_id;
  std::optional<double> travel_time_seconds;
  bool reachable{};
};

struct EngineResult {
  Point snapped_origin;
  double snap_distance_meters{};
  std::vector<ReachableEdge> reachable_edges;
  std::vector<Point> frontier;
  std::vector<DisplayPolygon> display_polygons;
  std::vector<FacilityTravelTime> facility_travel_times;
  std::size_t reachable_node_count{};
  std::size_t reachable_crossing_count{};
  std::vector<std::string> warnings;
};

[[nodiscard]] const char* edge_kind_name(EdgeKind kind);
void validate_graph(const EngineInput& input);
[[nodiscard]] EngineResult compute_reachability(const EngineInput& input);

}  // namespace isochrone
