#include <cmath>
#include <vector>

#include "isochrone/road_enclosure.hpp"
#include "isochrone/ring_builder.hpp"
#include "test_check.hpp"

using namespace isochrone;

namespace {

std::vector<RoadLink> square() {
  return {{0, 1, {{0, 0}, {200, 0}}},
          {1, 2, {{200, 0}, {200, 200}}},
          {2, 3, {{200, 200}, {0, 200}}},
          {3, 0, {{0, 200}, {0, 0}}}};
}

}  // namespace

int main() {
  const auto closed = closed_road_faces(square(), 4);
  TEST_CHECK(closed.size() == 1);
  TEST_CHECK(std::abs(signed_area(closed[0].ring) - 40000.0) < 1e-6);
  TEST_CHECK(closed[0].link_indices.size() == 4);
  TEST_CHECK(contains_point(closed[0].ring, {100, 100}));

  auto broken = square();
  broken.pop_back();
  TEST_CHECK(closed_road_faces(broken, 4).empty());

  // A dangling branch inside the block does not break its true perimeter.
  auto branched = square();
  branched.push_back({1, 4, {{200, 0}, {100, 100}}});
  TEST_CHECK(closed_road_faces(branched, 5).size() == 1);

  // Coordinates may touch or cross, but only matching node IDs close a face.
  std::vector<RoadLink> false_closure = {
      {0, 1, {{0, 0}, {200, 0}}},
      {2, 3, {{200, 0}, {200, 200}}},
      {4, 5, {{200, 200}, {0, 200}}},
      {6, 7, {{0, 200}, {0, 0}}},
  };
  TEST_CHECK(closed_road_faces(false_closure, 8).empty());

  std::vector<RoadLink> crossing_lines = {
      {0, 1, {{-10, 0}, {10, 0}}},
      {2, 3, {{0, -10}, {0, 10}}},
  };
  TEST_CHECK(closed_road_faces(crossing_lines, 4).empty());
}
