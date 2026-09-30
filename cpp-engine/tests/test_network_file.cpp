#include <fstream>
#include <iostream>
#include <stdexcept>

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
    TEST_CHECK(normal.nodes.size() == 10438);
    TEST_CHECK(normal.edges.size() == 10917);
    std::size_t junction_turns = 0;
    std::size_t junction_crossings = 0;
    for (const auto& edge : normal.edges) {
      if (edge.id.rfind("manual-junction:blue-crossroads-01:", 0) != 0) continue;
      if (edge.kind == isochrone::EdgeKind::turn) ++junction_turns;
      if (edge.kind == isochrone::EdgeKind::crossing) {
        ++junction_crossings;
        TEST_CHECK(edge.wait_seconds && *edge.wait_seconds == 20);
      }
    }
    TEST_CHECK(junction_turns == 4);
    TEST_CHECK(junction_crossings == 6);
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
