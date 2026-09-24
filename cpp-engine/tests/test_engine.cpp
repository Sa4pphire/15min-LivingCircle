#include <cmath>
#include <stdexcept>
#include <string>

#include "isochrone/engine.hpp"
#include "isochrone/json_io.hpp"
#include "test_check.hpp"

using namespace isochrone;

namespace {

bool near(double a, double b, double tolerance = 1e-6) {
  return std::abs(a - b) <= tolerance;
}

WalkEdge sidewalk(std::string id, std::string from, std::string to,
                  Point a, Point b, std::string block, std::string side) {
  WalkEdge edge;
  edge.id = std::move(id);
  edge.from = std::move(from);
  edge.to = std::move(to);
  edge.kind = EdgeKind::sidewalk;
  edge.path = {a, b};
  edge.street_block_id = std::move(block);
  edge.side = std::move(side);
  return edge;
}

WalkEdge connector(std::string id, std::string from, std::string to,
                   Point a, Point b, EdgeKind kind) {
  WalkEdge edge;
  edge.id = std::move(id);
  edge.from = std::move(from);
  edge.to = std::move(to);
  edge.kind = kind;
  edge.path = {a, b};
  return edge;
}

WalkEdge shared(std::string id, std::string from, std::string to,
                Point a, Point b, double width = 8.0) {
  WalkEdge edge = connector(std::move(id), std::move(from), std::move(to),
                            a, b, EdgeKind::shared_way);
  edge.street_block_id = "shared-block";
  edge.shared_way_type = "shared_alley";
  edge.width_meters = width;
  return edge;
}

void shared_side_access() {
  EngineInput input;
  input.origin = {50, 4};
  input.origin_edge_id = "shared";
  input.nodes = {{"a", {0, 0}}, {"b", {100, 0}}};
  input.edges = {shared("shared", "a", "b", {0, 0}, {100, 0})};
  input.facilities = {{"opposite", "shared", {50, -4}},
                      {"same", "shared", {50, 4}}};
  const EngineResult result = compute_reachability(input);
  TEST_CHECK(result.facility_travel_times.size() == 2);
  TEST_CHECK(result.facility_travel_times[0].travel_time_seconds.has_value());
  TEST_CHECK(near(*result.facility_travel_times[0].travel_time_seconds,
                  8.0 / 1.3));
  TEST_CHECK(near(*result.facility_travel_times[1].travel_time_seconds, 0.0));
  TEST_CHECK(result.reachable_edges[0].kind == EdgeKind::shared_way);
}

void ordinary_crossing_and_turn() {
  EngineInput input;
  input.origin = {0, 0};
  input.origin_edge_id = "south";
  input.nodes = {{"s0", {0, 0}}, {"s1", {100, 0}},
                 {"n0", {0, 20}}, {"n1", {100, 20}}};
  input.edges = {sidewalk("south", "s0", "s1", {0, 0}, {100, 0}, "road", "left"),
                 sidewalk("north", "n0", "n1", {0, 20}, {100, 20}, "road", "right")};
  input.facilities = {{"across", "north", {50, 20}}};
  const auto disconnected = compute_reachability(input);
  TEST_CHECK(!disconnected.facility_travel_times[0].travel_time_seconds);
  EngineInput fake_turn = input;
  fake_turn.edges.push_back(connector("fake-turn", "s0", "n0", {0, 0},
                                       {0, 20}, EdgeKind::turn));
  bool rejected_fake_turn = false;
  try { validate_graph(fake_turn); }
  catch (const std::invalid_argument&) { rejected_fake_turn = true; }
  TEST_CHECK(rejected_fake_turn);
  input.edges.push_back(connector("cross", "s0", "n0", {0, 0}, {0, 20},
                                   EdgeKind::crossing));
  const auto crossed = compute_reachability(input);
  TEST_CHECK(near(*crossed.facility_travel_times[0].travel_time_seconds,
                  70.0 / 1.3 + 20.0));
  TEST_CHECK(crossed.reachable_crossing_count == 1);

  EngineInput invalid_sides;
  invalid_sides.origin = {0, 0};
  invalid_sides.nodes = {{"a", {0, 0}}, {"b", {10, 0}},
                         {"c", {10, 20}}};
  invalid_sides.edges = {
      sidewalk("left", "a", "b", {0, 0}, {10, 0}, "block", "left"),
      sidewalk("right", "a", "c", {0, 0}, {10, 20}, "block", "right")};
  bool invalid_shared_node = false;
  try { validate_graph(invalid_sides); }
  catch (const std::invalid_argument&) { invalid_shared_node = true; }
  TEST_CHECK(invalid_shared_node);

  EngineInput turn;
  turn.origin = {0, 0};
  turn.origin_edge_id = "west";
  turn.nodes = {{"a", {0, 0}}, {"b", {10, 0}},
                {"c", {10, -10}}, {"d", {10, -20}}};
  turn.edges = {sidewalk("west", "a", "b", {0, 0}, {10, 0}, "west", "left"),
                connector("right-turn", "b", "c", {10, 0}, {10, -10},
                          EdgeKind::turn),
                sidewalk("south", "c", "d", {10, -10}, {10, -20},
                         "south", "left")};
  turn.facilities = {{"after-turn", "south", {10, -20}}};
  const auto turned = compute_reachability(turn);
  TEST_CHECK(near(*turned.facility_travel_times[0].travel_time_seconds,
                  30.0 / 1.3));
}

void geometry_does_not_connect() {
  EngineInput input;
  input.origin = {-10, 0};
  input.origin_edge_id = "shared";
  input.nodes = {{"a", {-10, 0}}, {"b", {10, 0}},
                 {"c", {0, -10}}, {"d", {0, 10}}};
  input.edges = {shared("shared", "a", "b", {-10, 0}, {10, 0}, 2),
                 sidewalk("vertical", "c", "d", {0, -10}, {0, 10},
                          "other", "left")};
  input.facilities = {{"unconnected", "vertical", {0, 8}}};
  const auto result = compute_reachability(input);
  TEST_CHECK(!result.facility_travel_times[0].travel_time_seconds);
}

void threshold_and_origin_side() {
  EngineInput input;
  input.origin = {0, 0};
  input.origin_edge_id = "long";
  input.nodes = {{"a", {0, 0}}, {"b", {1300, 0}}};
  input.edges = {sidewalk("long", "a", "b", {0, 0}, {1300, 0},
                          "road", "left")};
  input.facilities = {{"far", "long", {1300, 0}}};
  const auto result = compute_reachability(input);
  TEST_CHECK(!result.facility_travel_times[0].reachable);
  TEST_CHECK(near(*result.facility_travel_times[0].travel_time_seconds, 1000));
  TEST_CHECK(result.frontier.size() == 1);
  TEST_CHECK(near(result.frontier[0].x, 1170));
  TEST_CHECK(near(result.reachable_edges[0].path.back().x, 1170));

  input.origin = {0, 10};
  input.origin_edge_id.reset();
  input.nodes.push_back({"c", {0, 20}});
  input.nodes.push_back({"d", {1300, 20}});
  input.edges.push_back(sidewalk("opposite", "c", "d", {0, 20},
                                 {1300, 20}, "road", "right"));
  bool ambiguous = false;
  try { (void)compute_reachability(input); }
  catch (const AmbiguousOriginSide&) { ambiguous = true; }
  TEST_CHECK(ambiguous);
  input.origin = {0, 0};
  input.origin_edge_id = "opposite";
  bool wrong_side = false;
  try { (void)compute_reachability(input); }
  catch (const OriginNotOnWalkway&) { wrong_side = true; }
  TEST_CHECK(wrong_side);
  input.origin = {0, 8};
  input.origin_edge_id = "long";
  input.max_origin_snap_meters = 5.0;
  bool too_far = false;
  try { (void)compute_reachability(input); }
  catch (const OriginNotOnWalkway&) { too_far = true; }
  TEST_CHECK(too_far);
}

void crossing_endpoint_and_polygon_hole() {
  EngineInput crossing;
  crossing.origin = {0, 0};
  crossing.origin_edge_id = "start";
  crossing.walking_speed_meters_per_second = 1.0;
  crossing.threshold_seconds = 30.0;
  crossing.nodes = {{"a", {0, 0}}, {"a1", {-1, 0}},
                    {"b", {0, 10}}, {"b1", {1, 10}}};
  crossing.edges = {sidewalk("start", "a", "a1", {0, 0}, {-1, 0},
                             "south", "left"),
                    connector("cross", "a", "b", {0, 0}, {0, 10},
                              EdgeKind::crossing),
                    sidewalk("finish", "b", "b1", {0, 10}, {1, 10},
                             "north", "right")};
  const auto exact = compute_reachability(crossing);
  TEST_CHECK(exact.reachable_crossing_count == 1);
  bool found_endpoint = false;
  for (const Point point : exact.frontier) {
    if (near(point.x, 0) && near(point.y, 10)) found_endpoint = true;
  }
  TEST_CHECK(found_endpoint);
  crossing.threshold_seconds = 29.0;
  TEST_CHECK(compute_reachability(crossing).reachable_crossing_count == 0);

  EngineInput loop;
  loop.origin = {0, 0};
  loop.origin_edge_id = "bottom";
  loop.nodes = {{"a", {0, 0}}, {"b", {100, 0}},
                {"c", {100, 100}}, {"d", {0, 100}}};
  loop.edges = {sidewalk("bottom", "a", "b", {0, 0}, {100, 0},
                         "bottom", "left"),
                sidewalk("right", "b", "c", {100, 0}, {100, 100},
                         "right", "left"),
                sidewalk("top", "c", "d", {100, 100}, {0, 100},
                         "top", "left"),
                sidewalk("left", "d", "a", {0, 100}, {0, 0},
                         "left", "left")};
  const auto ring_result = compute_reachability(loop);
  TEST_CHECK(!ring_result.display_polygons.empty());
  TEST_CHECK(!ring_result.display_polygons[0].holes.empty());
}

void json_contract() {
  const std::string request = R"({"schemaVersion":2,"originMeters":{"xMeters":50,"yMeters":4},"originEdgeId":"shared","thresholdSeconds":900,"walkingSpeedMetersPerSecond":1.3,"crossingWaitSeconds":20,"nodes":[{"id":"a","xMeters":0,"yMeters":0},{"id":"b","xMeters":100,"yMeters":0}],"edges":[{"id":"shared","from":"a","to":"b","kind":"shared_way","streetBlockId":"shared-block","sharedWayType":"pedestrian_street","widthMeters":8,"pathMeters":[[0,0],[100,0]]}],"facilities":[{"id":"shop","accessEdgeId":"shared","accessPointMeters":[50,-4]}]})";
  const EngineInput input = parse_engine_input(request);
  const EngineResult result = compute_reachability(input);
  const std::string output = serialize_engine_result(result);
  TEST_CHECK(output.find("\"facilityTravelTimes\"") != std::string::npos);
  TEST_CHECK(output.find("\"accessEdgeId\":\"shared\"") != std::string::npos);
  TEST_CHECK(output.find("\"displayPolygonMeters\":[[[") != std::string::npos);
  TEST_CHECK(output.find("\"displayGeometryMeters\":{\"type\":\"MultiPolygon\",\"coordinates\":[[[")
             != std::string::npos);
  TEST_CHECK(output.find("\"widthMeters\":8") != std::string::npos);
}

void crossing_wait_override() {
  EngineInput input;
  input.origin = {0, 0};
  input.origin_edge_id = "south";
  input.walking_speed_meters_per_second = 1.0;
  input.threshold_seconds = 25;
  input.nodes = {{"a", {0, 0}}, {"b", {-1, 0}},
                 {"c", {0, 10}}, {"d", {1, 10}}};
  input.edges = {sidewalk("south", "a", "b", {0, 0}, {-1, 0},
                          "south-block", "left"),
                 connector("cross", "a", "c", {0, 0}, {0, 10},
                           EdgeKind::crossing),
                 sidewalk("north", "c", "d", {0, 10}, {1, 10},
                          "north-block", "right")};
  input.facilities = {{"far", "north", {0, 10}}};
  TEST_CHECK(compute_reachability(input).reachable_crossing_count == 0);
  input.edges[1].wait_seconds = 5.0;
  const auto result = compute_reachability(input);
  TEST_CHECK(result.reachable_crossing_count == 1);
  TEST_CHECK(near(*result.facility_travel_times[0].travel_time_seconds, 15.0));
  input.edges[1].wait_seconds = -1.0;
  bool rejected = false;
  try { validate_graph(input); }
  catch (const std::invalid_argument&) { rejected = true; }
  TEST_CHECK(rejected);
}

void multi_entrance_and_gray_zone() {
  EngineInput input;
  input.origin = {0, 0};
  input.origin_edge_id = "walk";
  input.walking_speed_meters_per_second = 1.0;
  input.threshold_seconds = 25.0;
  input.nodes = {{"a", {0, 0}}, {"b", {100, 0}}};
  input.edges = {sidewalk("walk", "a", "b", {0, 0}, {100, 0},
                          "block", "left")};
  FacilityAccess shop;
  shop.id = "shop";
  shop.category = "shopping";
  shop.entrances = {{"near", "walk", {30, 0}, {{30, 0}, {30, 10}}},
                    {"far", "walk", {80, 0}, {}}};
  input.facilities = {shop};
  input.service_categories = {{"shopping", "reviewed_online"},
                              {"healthcare", "incomplete"}};
  const EngineResult result = compute_reachability(input);
  TEST_CHECK(result.facility_travel_times.size() == 1);
  TEST_CHECK(result.facility_travel_times[0].best_entrance_id == "near");
  TEST_CHECK(near(*result.facility_travel_times[0].travel_time_seconds, 40.0));
  TEST_CHECK(!result.facility_travel_times[0].reachable);
  TEST_CHECK(result.gray_zones.size() == 2);
  TEST_CHECK(result.gray_zones[0].status == "candidate");
  TEST_CHECK(near(result.gray_zones[0].reachable_length_meters, 25.0));
  TEST_CHECK(near(result.gray_zones[0].uncovered_length_meters, 15.0));
  TEST_CHECK(result.gray_zones[0].uncovered_edges.size() == 1);
  TEST_CHECK(near(result.gray_zones[0].uncovered_edges[0].path.back().x, 15.0));
  TEST_CHECK(result.gray_zones[1].status == "data_insufficient");
  TEST_CHECK(result.gray_zones[1].uncovered_edges.empty());

  input.facilities[0].entrances[0].access_path = {{31, 0}, {30, 10}};
  bool invalid_path = false;
  try { validate_graph(input); }
  catch (const std::invalid_argument&) { invalid_path = true; }
  TEST_CHECK(invalid_path);
}

void json_extended_contract() {
  const std::string request = R"({"schemaVersion":2,"originMeters":{"xMeters":0,"yMeters":0},"originEdgeId":"walk","thresholdSeconds":25,"walkingSpeedMetersPerSecond":1,"crossingWaitSeconds":20,"nodes":[{"id":"a","xMeters":0,"yMeters":0},{"id":"b","xMeters":100,"yMeters":0}],"edges":[{"id":"walk","from":"a","to":"b","kind":"sidewalk","streetBlockId":"block","side":"left","pathMeters":[[0,0],[100,0]]}],"serviceCategories":[{"id":"shopping","dataStatus":"reviewed_online"}],"facilities":[{"id":"shop","category":"shopping","entrances":[{"id":"gate","accessEdgeId":"walk","streetAccessPointMeters":[30,0],"accessPathMeters":[[30,0],[30,10]]}]}]})";
  const EngineInput input = parse_engine_input(request);
  const EngineResult result = compute_reachability(input);
  const std::string output = serialize_engine_result(result);
  TEST_CHECK(output.find("\"bestEntranceId\":\"gate\"") != std::string::npos);
  TEST_CHECK(output.find("\"grayZones\":[{\"category\":\"shopping\"") != std::string::npos);
  TEST_CHECK(output.find("\"uncoveredLengthMeters\":15") != std::string::npos);
}

}  // namespace

int main() {
  shared_side_access();
  ordinary_crossing_and_turn();
  geometry_does_not_connect();
  threshold_and_origin_side();
  crossing_endpoint_and_polygon_hole();
  json_contract();
  crossing_wait_override();
  multi_entrance_and_gray_zone();
  json_extended_contract();
}
