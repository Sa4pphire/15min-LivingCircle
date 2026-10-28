#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "isochrone/road_graph.hpp"
#include "isochrone/types.hpp"

namespace isochrone {

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

struct RouteSegment {
  std::string edge_id;
  std::string kind;
  std::vector<Point> path;
  double travel_time_seconds{};
};

struct FacilityRoute {
  std::string facility_id;
  std::optional<std::string> entrance_id;
  bool connected{};
  bool within_threshold{};
  std::optional<double> travel_time_seconds;
  double length_meters{};
  double crossing_wait_seconds{};
  std::vector<Point> path;
  std::vector<RouteSegment> segments;
};

struct GrayZone {
  std::string category;
  std::string status;
  std::vector<ReachableEdge> uncovered_edges;
  std::vector<DisplayPolygon> display_polygons;
  double uncovered_length_meters{};
  double reachable_length_meters{};
};

struct LocalGrayZone {
  std::string category;
  std::vector<ReachableEdge> covered_edges;
  std::vector<ReachableEdge> candidate_uncovered_edges;
  std::vector<ReachableEdge> unknown_edges;
  double covered_length_meters{};
  double candidate_uncovered_length_meters{};
  double unknown_length_meters{};
  std::vector<std::string> warnings;
};

struct EngineResult {
  bool is_local_experiment{};
  Point snapped_origin;
  double snap_distance_meters{};
  double origin_access_seconds{};
  std::vector<ReachableEdge> reachable_edges;
  std::vector<Point> frontier;
  std::vector<DisplayPolygon> display_polygons;
  std::vector<FacilityTravelTime> facility_travel_times;
  std::vector<GrayZone> gray_zones;
  std::vector<LocalGrayZone> local_gray_zones;
  std::size_t reachable_node_count{};
  std::size_t reachable_crossing_count{};
  std::size_t closed_road_face_count{};
  std::size_t road_closure_filled_cell_count{};
  std::vector<std::string> warnings;
  std::optional<FacilityRoute> facility_route;
  std::vector<std::pair<std::string, double>> stage_timings_ms;
};

}  // namespace isochrone
