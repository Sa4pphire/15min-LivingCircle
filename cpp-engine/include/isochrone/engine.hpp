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
  std::optional<double> wait_seconds;
};

struct FacilityEntrance {
  std::string id;
  std::string access_edge_id;
  Point street_access_point;
  std::vector<Point> access_path;
};

struct FacilityAccess {
  std::string id;
  // Retained for legacy single-entrance v2 input.
  std::string access_edge_id;
  Point access_point;
  std::string category;
  std::vector<FacilityEntrance> entrances;
};

struct ServiceCategory {
  std::string id;
  std::string data_status;
};

struct EngineInput {
  int schema_version{2};
  Point origin;
  std::optional<std::string> origin_edge_id;
  double threshold_seconds{900.0};
  double walking_speed_meters_per_second{1.3};
  double crossing_wait_seconds{20.0};
  double max_origin_snap_meters{30.0};
  // Synthetic preview only: permit a straight-line, unverified connection
  // from an off-network origin to the nearest walkable edge.
  bool allow_off_network_origin{false};
  double display_buffer_meters{15.0};
  // Maximum visual extension from a reachable street into its surrounding block.
  double display_area_radius_meters{80.0};
  // Remove only small enclosed holes from the approximate isochrone surface.
  double display_min_hole_area_square_meters{2500.0};
  double display_grid_step_meters{10.0};
  std::vector<WalkNode> nodes;
  std::vector<WalkEdge> edges;
  std::vector<FacilityAccess> facilities;
  std::vector<ServiceCategory> service_categories;
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
  std::string category;
  std::optional<std::string> best_entrance_id;
};

struct GrayZone {
  std::string category;
  std::string status;
  std::vector<ReachableEdge> uncovered_edges;
  std::vector<DisplayPolygon> display_polygons;
  double uncovered_length_meters{};
  double reachable_length_meters{};
};

struct EngineResult {
  Point snapped_origin;
  double snap_distance_meters{};
  double origin_access_seconds{};
  std::vector<ReachableEdge> reachable_edges;
  std::vector<Point> frontier;
  std::vector<DisplayPolygon> display_polygons;
  std::vector<FacilityTravelTime> facility_travel_times;
  std::vector<GrayZone> gray_zones;
  std::size_t reachable_node_count{};
  std::size_t reachable_crossing_count{};
  std::vector<std::string> warnings;
};

[[nodiscard]] const char* edge_kind_name(EdgeKind kind);
void validate_graph(const EngineInput& input);
[[nodiscard]] EngineResult compute_reachability(const EngineInput& input);

}  // namespace isochrone
