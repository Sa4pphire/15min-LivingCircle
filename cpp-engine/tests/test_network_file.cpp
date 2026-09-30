#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <utility>

#include "isochrone/json_io.hpp"
#include "test_check.hpp"

#ifndef ISOCHRONE_SYNTHETIC_NETWORK_PATH
#define ISOCHRONE_SYNTHETIC_NETWORK_PATH "data/networks/synthetic-preview.json"
#endif

int main() {
  try {
    std::ifstream file(ISOCHRONE_SYNTHETIC_NETWORK_PATH, std::ios::binary);
    TEST_CHECK(file.good());
    const std::string document = isochrone::read_all(file);
    const auto normal = isochrone::parse_synthetic_network(document, {0, 0});
    // The fixture grows when reviewed junctions are added. Test its walking
    // semantics rather than freezing the previous generation's total size.
    TEST_CHECK(normal.nodes.size() > 8518);
    TEST_CHECK(normal.edges.size() > 9232);
    std::size_t junction_turns = 0;
    std::size_t junction_crossings = 0;
    std::map<std::string, std::pair<std::size_t, std::size_t>> reviewed_counts;
    std::map<std::string, std::pair<std::size_t, std::size_t>> crossroad_counts;
    for (const auto& edge : normal.edges) {
      const std::string crossroad_prefix = "manual-junction:crossroad-";
      if (edge.id.rfind(crossroad_prefix, 0) == 0) {
        const auto end = edge.id.find(':', crossroad_prefix.size());
        TEST_CHECK(end != std::string::npos);
        auto& counts = crossroad_counts[edge.id.substr(crossroad_prefix.size(), end-crossroad_prefix.size())];
        if (edge.kind == isochrone::EdgeKind::turn) {
          ++counts.first;
          TEST_CHECK(!edge.wait_seconds);
        } else {
          TEST_CHECK(edge.kind == isochrone::EdgeKind::crossing);
          ++counts.second;
          TEST_CHECK(edge.wait_seconds && *edge.wait_seconds == 20);
        }
      }
      const std::string prefix = "manual-junction:review-";
      if (edge.id.rfind(prefix, 0) == 0) {
        const auto end = edge.id.find(':', prefix.size());
        TEST_CHECK(end != std::string::npos);
        const auto name = edge.id.substr(prefix.size(), end - prefix.size());
        auto& counts = reviewed_counts[name];
        if (edge.kind == isochrone::EdgeKind::turn) {
          ++counts.first;
          TEST_CHECK(!edge.wait_seconds);
        } else {
          TEST_CHECK(edge.kind == isochrone::EdgeKind::crossing);
          ++counts.second;
          TEST_CHECK(edge.wait_seconds && *edge.wait_seconds == 20);
        }
      }
      if (edge.id.rfind("manual-junction:blue-crossroads-01:", 0) != 0) continue;
      if (edge.kind == isochrone::EdgeKind::turn) ++junction_turns;
      if (edge.kind == isochrone::EdgeKind::crossing) {
        ++junction_crossings;
        TEST_CHECK(edge.wait_seconds && *edge.wait_seconds == 20);
      }
    }
    TEST_CHECK(junction_turns == 4);
    TEST_CHECK(junction_crossings == 6);
    const std::map<std::string, std::pair<std::size_t, std::size_t>> expected_reviewed = {
      {"guofan-guoxiu", {4, 4}}, {"yinxing-guoquan", {4, 8}},
      {"yingao-guoquan", {4, 8}}, {"south-central", {4, 5}},
      {"southeast-outer", {4, 4}}, {"southeast-outer-2", {4, 4}},
      {"north-outer", {4, 4}}, {"central-shared", {4, 3}},
    };
    TEST_CHECK(reviewed_counts == expected_reviewed);
    TEST_CHECK(!crossroad_counts.empty());
    for (const auto& entry : crossroad_counts) {
      TEST_CHECK(entry.second.first == 4);
      TEST_CHECK(entry.second.second >= 2 && entry.second.second <= 8);
    }
    TEST_CHECK(normal.facilities.empty());
    TEST_CHECK(normal.service_categories.empty());
    TEST_CHECK(normal.threshold_seconds == 900);
    TEST_CHECK(normal.allow_off_network_origin);
    TEST_CHECK(!normal.local_experiment);

    const auto local = isochrone::parse_synthetic_network(
        document, {-54.25, -203.25}, "w:154811345:2:0", true);
    TEST_CHECK(local.threshold_seconds == 180);
    TEST_CHECK(!local.allow_off_network_origin);
    TEST_CHECK(local.max_origin_snap_meters == 5);
    TEST_CHECK(local.local_experiment->topology_status == "incomplete");
    TEST_CHECK(local.local_experiment->boundary_node_ids.empty());
    TEST_CHECK(local.nodes.size() == normal.nodes.size());
    TEST_CHECK(local.edges.size() == normal.edges.size());
    for (std::size_t index = 0; index < local.edges.size(); ++index) {
      TEST_CHECK(local.edges[index].id == normal.edges[index].id);
      TEST_CHECK(local.edges[index].from == normal.edges[index].from);
      TEST_CHECK(local.edges[index].to == normal.edges[index].to);
    }
    const auto result = isochrone::compute_reachability(local);
    TEST_CHECK(!result.reachable_edges.empty());
    TEST_CHECK(result.local_gray_zones.empty());
    TEST_CHECK(result.gray_zones.empty());
    TEST_CHECK(result.display_polygons.empty());
    TEST_CHECK(result.facility_travel_times.empty());

    std::string not_synthetic = document;
    const auto key_position = not_synthetic.find("\"synthetic\"");
    TEST_CHECK(key_position != std::string::npos);
    const auto position = not_synthetic.find("true", key_position);
    TEST_CHECK(position != std::string::npos);
    not_synthetic.replace(position, 4, "false");
    bool rejected = false;
    try {
      (void)isochrone::parse_synthetic_network(not_synthetic, {0, 0});
    } catch (const std::invalid_argument&) {
      rejected = true;
    }
    TEST_CHECK(rejected);
    std::cout << "Existing synthetic network file checks passed.\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
