#include "detail/input_validation.hpp"

#include <cmath>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "detail/origin_snap.hpp"
#include "detail/path_geometry.hpp"

namespace isochrone::detail {

void validate_schema_version(int schema_version) {
  if (schema_version != 2) {
    throw std::invalid_argument("invalid engine parameters");
  }
}

// Preserve the existing cross-domain validation order. This is deliberately
// not split into independently cached graph and request checks.
void validate_input(const RoadGraph& graph, const AnalysisRequest& request,
                    const ExecutionControl& control) {
  if (!finite(request.origin) ||
      !std::isfinite(request.threshold_seconds) ||
      request.threshold_seconds <= 0.0 ||
      !std::isfinite(request.walking_speed_meters_per_second) ||
      request.walking_speed_meters_per_second <= 0.0 ||
      !std::isfinite(request.crossing_wait_seconds) ||
      request.crossing_wait_seconds < 0.0 ||
      !std::isfinite(request.max_origin_snap_meters) ||
      request.max_origin_snap_meters <= 0.0 ||
      !std::isfinite(request.display_buffer_meters) ||
      request.display_buffer_meters <= 0.0 ||
      !std::isfinite(request.display_area_radius_meters) ||
      request.display_area_radius_meters <= 0.0 ||
      !std::isfinite(request.display_min_hole_area_square_meters) ||
      request.display_min_hole_area_square_meters < 0.0 ||
      !std::isfinite(request.display_grid_step_meters) ||
      request.display_grid_step_meters <= 0.0) {
    throw std::invalid_argument("invalid engine parameters");
  }
  if (graph.nodes.empty() || graph.edges.empty()) {
    throw std::invalid_argument("walking graph must contain nodes and edges");
  }
  std::unordered_map<std::string, Point> nodes;
  for (const auto& node : graph.nodes) {
    control.Poll();
    if (node.id.empty() || !finite(node.point) ||
        !nodes.emplace(node.id, node.point).second) {
      throw std::invalid_argument("invalid or duplicate walking node");
    }
  }
  if (request.local_experiment) {
    if (request.allow_off_network_origin ||
        (request.local_experiment->topology_status != "verified" &&
         request.local_experiment->topology_status != "incomplete")) {
      throw std::invalid_argument("invalid local experiment parameters");
    }
    std::unordered_set<std::string> boundary_ids;
    for (const auto& id : request.local_experiment->boundary_node_ids) {
      control.Poll();
      if (!nodes.count(id) || !boundary_ids.insert(id).second) {
        throw std::invalid_argument("invalid or duplicate boundaryNodeId");
      }
    }
  }
  std::unordered_set<std::string> edge_ids;
  std::unordered_map<std::string, const WalkEdge*> edge_lookup;
  std::unordered_map<std::string, int> block_kinds;
  std::unordered_map<std::string, std::unordered_map<std::string, std::string>>
      block_node_sides;
  std::unordered_map<std::string, std::unordered_map<std::string, std::string>>
      node_block_sides;
  for (const auto& edge : graph.edges) {
    control.Poll();
    if (edge.id.empty() || !edge_ids.insert(edge.id).second ||
        edge.from == edge.to || !nodes.count(edge.from) ||
        !nodes.count(edge.to) || edge.path.size() < 2 ||
        edge.path.size() > 1000) {
      throw std::invalid_argument("invalid walking edge identity or endpoints");
    }
    edge_lookup.emplace(edge.id, &edge);
    for (Point point : edge.path) {
      control.Poll();
      if (!finite(point)) throw std::invalid_argument("invalid edge coordinate");
    }
    if (distance(edge.path.front(), nodes.at(edge.from)) > kEndpointTolerance ||
        distance(edge.path.back(), nodes.at(edge.to)) > kEndpointTolerance ||
        path_length(edge.path, control) <= kEpsilon) {
      throw std::invalid_argument("edge path does not match its nodes");
    }
    if (edge.kind == EdgeKind::walkway && !is_shared_walkway(edge) &&
        (edge.street_block_id.empty() ||
         (edge.side != "left" && edge.side != "right"))) {
      throw std::invalid_argument("separated walkway needs streetBlockId and side");
    }
    if (is_shared_walkway(edge) &&
        (edge.street_block_id.empty() || !edge.side.empty() ||
         (edge.shared_way_type != "pedestrian_street" &&
          edge.shared_way_type != "shared_alley") ||
         !std::isfinite(edge.width_meters) || edge.width_meters <= 0.0)) {
      throw std::invalid_argument(
          "shared walkway needs streetBlockId, sharedWayType and positive widthMeters, without side");
    }
    if (!is_shared_walkway(edge) &&
        (!edge.shared_way_type.empty() || edge.width_meters != 0.0)) {
      throw std::invalid_argument("only shared accessMode may declare sharedWayType or widthMeters");
    }
    if (edge.wait_seconds &&
        (edge.kind != EdgeKind::crossing || !std::isfinite(*edge.wait_seconds) ||
         *edge.wait_seconds < 0.0)) {
      throw std::invalid_argument("waitSeconds must be non-negative and only on crossing");
    }
    if (edge.kind == EdgeKind::walkway) {
      const auto [found, inserted] = block_kinds.emplace(edge.street_block_id,
          is_shared_walkway(edge) ? 1 : 0);
      if (!inserted && found->second != (is_shared_walkway(edge) ? 1 : 0)) {
        throw std::invalid_argument("streetBlockId mixes separated and shared road models");
      }
    }
    if (edge.kind == EdgeKind::walkway && !is_shared_walkway(edge)) {
      for (const auto& node_id : {edge.from, edge.to}) {
        control.Poll();
        const auto [found, inserted] =
            block_node_sides[edge.street_block_id].emplace(node_id, edge.side);
        if (!inserted && found->second != edge.side) {
          throw std::invalid_argument(
              "opposite sidewalk sides of one streetBlockId share a node");
        }
        node_block_sides[node_id][edge.street_block_id] = edge.side;
      }
    }
  }
  for (const auto& edge : graph.edges) {
    control.Poll();
    if (edge.kind != EdgeKind::turn) continue;
    const auto from = node_block_sides.find(edge.from);
    const auto to = node_block_sides.find(edge.to);
    if (from == node_block_sides.end() || to == node_block_sides.end()) continue;
    for (const auto& [block, side] : from->second) {
      control.Poll();
      const auto opposite = to->second.find(block);
      if (opposite != to->second.end() && opposite->second != side) {
        throw std::invalid_argument(
            "turn connects opposite sides of one streetBlockId; use crossing");
      }
    }
  }
  std::unordered_set<std::string> category_ids;
  for (const auto& category : request.service_categories) {
    control.Poll();
    if (category.id.empty() || !category_ids.insert(category.id).second ||
        (category.data_status != "reviewed_online" &&
         category.data_status != "incomplete") ||
        (category.local_inventory_status != "verified" &&
         category.local_inventory_status != "incomplete")) {
      throw std::invalid_argument("invalid or duplicate service category");
    }
  }
  std::unordered_set<std::string> facility_ids;
  for (const auto& facility : request.facilities) {
    control.Poll();
    if (facility.id.empty() || !facility_ids.insert(facility.id).second ||
        (!facility.category.empty() && !category_ids.count(facility.category))) {
      throw std::invalid_argument("invalid or duplicate facility access");
    }
    if (!facility.entrances.empty() && !facility.access_edge_id.empty()) {
      throw std::invalid_argument("facility cannot mix legacy and entrance fields");
    }
    if (facility.entrances.empty() &&
        (facility.access_edge_id.empty() || !finite(facility.access_point))) {
      throw std::invalid_argument("facility needs an entrance");
    }
    std::unordered_set<std::string> entrance_ids;
    std::vector<FacilityEntrance> legacy;
    if (facility.entrances.empty()) {
      legacy.push_back({facility.id, facility.access_edge_id,
                        facility.access_point, {}});
    }
    const auto& entrances = facility.entrances.empty() ? legacy : facility.entrances;
    for (const auto& entrance : entrances) {
      control.Poll();
      if (entrance.id.empty() || !entrance_ids.insert(entrance.id).second ||
          !finite(entrance.street_access_point)) {
        throw std::invalid_argument("invalid or duplicate facility entrance");
      }
      const auto edge = edge_lookup.find(entrance.access_edge_id);
      if (edge == edge_lookup.end() ||
          edge->second->kind != EdgeKind::walkway) {
        throw std::invalid_argument("facility accessEdgeId must identify a traversable edge");
      }
      const Snap access = project_to_edge(
          entrance.street_access_point, *edge->second, 0, control);
      if (access.surface_distance_meters > kFacilityAccessTolerance) {
        throw std::invalid_argument("facility street access is outside its walkable edge");
      }
      if (!entrance.access_path.empty()) {
        if (entrance.access_path.size() < 2 ||
            entrance.access_path.size() > 1000 ||
            distance(entrance.access_path.front(), entrance.street_access_point) >
                kEndpointTolerance ||
            path_length(entrance.access_path, control) <= kEpsilon) {
          throw std::invalid_argument("invalid facility accessPathMeters");
        }
        for (Point point : entrance.access_path) {
          control.Poll();
          if (!finite(point)) {
            throw std::invalid_argument("invalid facility access path coordinate");
          }
        }
      }
    }
  }
}

}  // namespace isochrone::detail
