#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "isochrone/engine.hpp"
#include "isochrone/json_io.hpp"
#include "test_check.hpp"

using namespace isochrone;

namespace {

bool near(double actual, double expected) {
  return std::abs(actual - expected) <= 1e-6;
}

double path_length(const std::vector<Point>& path) {
  double length = 0.0;
  for (std::size_t i = 1; i < path.size(); ++i) {
    length += std::hypot(path[i].x - path[i - 1].x,
                         path[i].y - path[i - 1].y);
  }
  return length;
}

double edge_length(const std::vector<ReachableEdge>& edges,
                   const std::string& edge_id) {
  double length = 0.0;
  for (const auto& edge : edges) {
    if (edge.edge_id == edge_id) length += path_length(edge.path);
  }
  return length;
}

bool has_interval(const std::vector<ReachableEdge>& edges,
                  const std::string& edge_id, double first_x,
                  double last_x) {
  for (const auto& edge : edges) {
    if (edge.edge_id == edge_id && !edge.path.empty() &&
        near(edge.path.front().x, first_x) &&
        near(edge.path.back().x, last_x)) {
      return true;
    }
  }
  return false;
}

bool has_warning(const std::vector<std::string>& warnings,
                 const std::string& warning) {
  for (const auto& value : warnings) {
    if (value == warning) return true;
  }
  return false;
}

WalkEdge sidewalk(std::string id, std::string from, std::string to,
                  Point start, Point end, std::string block,
                  std::string side) {
  WalkEdge edge;
  edge.id = std::move(id);
  edge.from = std::move(from);
  edge.to = std::move(to);
  edge.kind = EdgeKind::sidewalk;
  edge.path = {start, end};
  edge.street_block_id = std::move(block);
  edge.side = std::move(side);
  return edge;
}

EngineInput line_input(double length, double origin_x = 0.0) {
  EngineInput input;
  input.origin = {origin_x, 0.0};
  input.origin_edge_id = "walk";
  input.threshold_seconds = 180.0;
  input.walking_speed_meters_per_second = 1.0;
  input.display_buffer_meters = 5.0;
  input.display_area_radius_meters = 5.0;
  input.display_grid_step_meters = 10.0;
  input.nodes = {{"a", {0, 0}}, {"b", {length, 0}}};
  input.edges = {sidewalk("walk", "a", "b", {0, 0}, {length, 0},
                          "block", "left")};
  input.service_categories = {{"healthcare", "incomplete", "verified"}};
  input.local_experiment = LocalExperiment{{}, "verified"};
  return input;
}

const LocalGrayZone& only_zone(const EngineResult& result) {
  TEST_CHECK(result.local_gray_zones.size() == 1);
  TEST_CHECK(result.gray_zones.empty());
  TEST_CHECK(result.local_gray_zones[0].category == "healthcare");
  return result.local_gray_zones[0];
}

void boundary_splits_an_edge_interior() {
  EngineInput input = line_input(400.0, 200.0);
  input.local_experiment->boundary_node_ids = {"b"};
  const EngineResult result = compute_reachability(input);
  const LocalGrayZone& zone = only_zone(result);
  // Origin reaches [20,380]. An outside facility might serve [220,380]
  // through the cut at x=400; [20,220] is locally unserved.
  TEST_CHECK(near(zone.covered_length_meters, 0.0));
  TEST_CHECK(near(zone.candidate_uncovered_length_meters, 200.0));
  TEST_CHECK(near(zone.unknown_length_meters, 160.0));
  TEST_CHECK(has_interval(zone.candidate_uncovered_edges, "walk", 20, 200));
  TEST_CHECK(has_interval(zone.candidate_uncovered_edges, "walk", 200, 220));
  TEST_CHECK(has_interval(zone.unknown_edges, "walk", 220, 380));
  TEST_CHECK(!has_warning(result.warnings,
                          "LOCAL_REACHABILITY_MAY_BE_TRUNCATED"));
}

void reachable_cut_boundary_is_not_a_real_gray_zone() {
  EngineInput input = line_input(100.0);
  input.local_experiment->boundary_node_ids = {"b"};
  const EngineResult result = compute_reachability(input);
  const LocalGrayZone& zone = only_zone(result);
  TEST_CHECK(near(zone.candidate_uncovered_length_meters, 0.0));
  TEST_CHECK(near(zone.unknown_length_meters, 100.0));
  TEST_CHECK(has_warning(result.warnings,
                         "LOCAL_REACHABILITY_MAY_BE_TRUNCATED"));
}

void verified_true_dead_end_is_locally_uncovered() {
  EngineInput input = line_input(200.0);
  // No cut exits: the degree-one endpoint is a surveyed real dead end.
  const EngineResult result = compute_reachability(input);
  const LocalGrayZone& zone = only_zone(result);
  TEST_CHECK(near(zone.candidate_uncovered_length_meters, 180.0));
  TEST_CHECK(near(zone.unknown_length_meters, 0.0));
  TEST_CHECK(has_interval(zone.candidate_uncovered_edges, "walk", 0, 180));
}

void missing_verification_makes_unserved_streets_unknown() {
  EngineInput input = line_input(200.0);
  input.service_categories[0].local_inventory_status = "incomplete";
  const EngineResult inventory_result = compute_reachability(input);
  const LocalGrayZone& unreviewed_inventory = only_zone(inventory_result);
  TEST_CHECK(near(unreviewed_inventory.candidate_uncovered_length_meters, 0.0));
  TEST_CHECK(near(unreviewed_inventory.unknown_length_meters, 180.0));
  input.service_categories[0].local_inventory_status = "verified";
  input.local_experiment->topology_status = "incomplete";
  const EngineResult topology_result = compute_reachability(input);
  const LocalGrayZone& unreviewed_topology = only_zone(topology_result);
  TEST_CHECK(near(unreviewed_topology.candidate_uncovered_length_meters, 0.0));
  TEST_CHECK(near(unreviewed_topology.unknown_length_meters, 180.0));
}

void multiple_entrances_cover_two_disjoint_street_parts() {
  EngineInput input = line_input(600.0, 300.0);
  FacilityAccess facility;
  facility.id = "clinic";
  facility.category = "healthcare";
  facility.entrances = {{"west", "walk", {100, 0}, {{100, 0}, {100, 20}}},
                        {"east", "walk", {500, 0}, {}}};
  input.facilities = {facility};
  const EngineResult result = compute_reachability(input);
  const LocalGrayZone& zone = only_zone(result);
  // R=[120,480]. West entrance has 20s access and serves to x=260;
  // east entrance serves from x=320. The interior 60m gap is not covered.
  TEST_CHECK(near(zone.covered_length_meters, 300.0));
  TEST_CHECK(near(zone.candidate_uncovered_length_meters, 60.0));
  TEST_CHECK(near(zone.unknown_length_meters, 0.0));
  TEST_CHECK(has_interval(zone.candidate_uncovered_edges, "walk", 260, 300));
  TEST_CHECK(has_interval(zone.candidate_uncovered_edges, "walk", 300, 320));
  TEST_CHECK(result.facility_travel_times.size() == 1);
  TEST_CHECK(result.facility_travel_times[0].best_entrance_id == "east");
  TEST_CHECK(near(*result.facility_travel_times[0].travel_time_seconds,
                  200.0));
}

void crossing_wait_changes_service_coverage() {
  EngineInput input;
  input.origin = {0, 0};
  input.origin_edge_id = "south";
  input.threshold_seconds = 180;
  input.walking_speed_meters_per_second = 1.0;
  input.display_buffer_meters = 5.0;
  input.display_area_radius_meters = 5.0;
  input.display_grid_step_meters = 10.0;
  input.nodes = {{"s0", {0, 0}}, {"s1", {100, 0}},
                 {"n0", {100, 10}}, {"n1", {200, 10}}};
  input.edges = {sidewalk("south", "s0", "s1", {0, 0}, {100, 0},
                          "road", "left"),
                 sidewalk("north", "n0", "n1", {100, 10}, {200, 10},
                          "road", "right")};
  WalkEdge crossing;
  crossing.id = "cross";
  crossing.from = "s1";
  crossing.to = "n0";
  crossing.kind = EdgeKind::crossing;
  crossing.path = {{100, 0}, {100, 10}};
  input.edges.push_back(crossing);
  input.facilities = {{"clinic", "north", {200, 10}, "healthcare", {}}};
  input.service_categories = {{"healthcare", "incomplete", "verified"}};
  input.local_experiment = LocalExperiment{{}, "verified"};
  const EngineResult normal_result = compute_reachability(input);
  const LocalGrayZone& normal = only_zone(normal_result);
  TEST_CHECK(near(edge_length(normal.covered_edges, "south"), 50.0));
  TEST_CHECK(near(edge_length(normal.candidate_uncovered_edges, "south"),
                  50.0));
  input.edges[2].wait_seconds = 70.0;
  const EngineResult delayed_result = compute_reachability(input);
  const LocalGrayZone& delayed = only_zone(delayed_result);
  TEST_CHECK(near(edge_length(delayed.covered_edges, "south"), 0.0));
  TEST_CHECK(near(edge_length(delayed.candidate_uncovered_edges, "south"),
                  100.0));
  // The uncertainty wave from a cut boundary must pay the same crossing
  // wait, rather than leaking for free onto the opposite sidewalk.
  input.facilities.clear();
  input.local_experiment->boundary_node_ids = {"n1"};
  input.edges[2].wait_seconds.reset();
  const auto boundary_normal = compute_reachability(input);
  TEST_CHECK(near(edge_length(only_zone(boundary_normal).unknown_edges, "south"), 50.0));
  input.edges[2].wait_seconds = 70.0;
  const auto boundary_delayed = compute_reachability(input);
  TEST_CHECK(near(edge_length(only_zone(boundary_delayed).unknown_edges, "south"), 0.0));
}

void json_round_trip_and_invalid_boundary() {
  const std::string request = R"({"schemaVersion":2,"originMeters":{"xMeters":0,"yMeters":0},"originEdgeId":"walk","walkingSpeedMetersPerSecond":1,"crossingWaitSeconds":20,"localExperiment":{"boundaryNodeIds":["b"],"topologyStatus":"verified"},"nodes":[{"id":"a","xMeters":0,"yMeters":0},{"id":"b","xMeters":200,"yMeters":0}],"edges":[{"id":"walk","from":"a","to":"b","kind":"sidewalk","streetBlockId":"block","side":"left","pathMeters":[[0,0],[200,0]]}],"serviceCategories":[{"id":"healthcare","dataStatus":"incomplete","localInventoryStatus":"verified"}]})";
  EngineInput input = parse_engine_input(request);
  TEST_CHECK(input.local_experiment.has_value());
  TEST_CHECK(near(input.threshold_seconds, 180.0));
  TEST_CHECK(input.local_experiment->boundary_node_ids.size() == 1);
  TEST_CHECK(input.service_categories[0].local_inventory_status == "verified");
  const std::string output = serialize_engine_result(compute_reachability(input));
  TEST_CHECK(output.find("\"grayZones\":[]") != std::string::npos);
  TEST_CHECK(output.find("\"localGrayZones\":[{") != std::string::npos);
  TEST_CHECK(output.find("\"coveredEdges\"") != std::string::npos);
  TEST_CHECK(output.find("\"candidateUncoveredEdges\"") !=
             std::string::npos);
  TEST_CHECK(output.find("\"unknownEdges\"") != std::string::npos);
  std::string incomplete_request = request;
  const std::string topology_field = ",\"topologyStatus\":\"verified\"";
  incomplete_request.erase(incomplete_request.find(topology_field), topology_field.size());
  const EngineInput incomplete = parse_engine_input(incomplete_request);
  TEST_CHECK(incomplete.local_experiment->topology_status == "incomplete");
  const EngineResult unverified = compute_reachability(incomplete);
  TEST_CHECK(only_zone(unverified).candidate_uncovered_edges.empty());
  TEST_CHECK(near(only_zone(unverified).unknown_length_meters, 180.0));
  input.local_experiment->boundary_node_ids = {"not-a-node"};
  bool unknown_boundary_rejected = false;
  try { validate_graph(input); }
  catch (const std::invalid_argument&) { unknown_boundary_rejected = true; }
  TEST_CHECK(unknown_boundary_rejected);
}

}  // namespace

int main() {
  boundary_splits_an_edge_interior();
  reachable_cut_boundary_is_not_a_real_gray_zone();
  verified_true_dead_end_is_locally_uncovered();
  missing_verification_makes_unserved_streets_unknown();
  multiple_entrances_cover_two_disjoint_street_parts();
  crossing_wait_changes_service_coverage();
  json_round_trip_and_invalid_boundary();
}
