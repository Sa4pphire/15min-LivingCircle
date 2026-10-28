#pragma once

#include <optional>
#include <string>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

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
  // Independent of online POI review: completeness inside the local patch.
  std::string local_inventory_status{"incomplete"};
};

struct LocalExperiment {
  // Every point at which the surveyed graph was cut, not genuine dead ends.
  std::vector<std::string> boundary_node_ids;
  // "verified" also asserts that every cut exit has been marked.
  std::string topology_status{"incomplete"};
};

// Parameters and facilities belong to one analysis, even when the base graph is reused.
struct AnalysisRequest {
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
  std::vector<FacilityAccess> facilities;
  std::vector<ServiceCategory> service_categories;
  // Presence still activates local analysis; keep its topology evidence with
  // this request until graph metadata and versioning are designed separately.
  std::optional<LocalExperiment> local_experiment;
  // Optional, backward-compatible on-demand route using the same split graph.
  std::optional<std::string> route_facility_id;
  bool route_only{false};
  // POI enrichment: compute all facility times, without surfaces or coverage.
  bool facilities_only{false};
};

}  // namespace isochrone
