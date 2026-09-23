#include <cmath>
#include <vector>

#include "isochrone/ring_builder.hpp"
#include "test_check.hpp"

int main() {
  const std::vector<isochrone::Segment> segments{
      {{0.0, 0.0}, {1.0, 0.0}},
      {{1.0, 1.0}, {0.0, 1.0}},
      {{1.0, 0.0}, {1.0, 1.0}},
      {{0.0, 1.0}, {0.0, 0.0}},
  };

  const auto rings = isochrone::build_rings(segments);
  TEST_CHECK(rings.size() == 1);
  TEST_CHECK(std::abs(isochrone::signed_area(rings.front())) == 1.0);
  TEST_CHECK(isochrone::contains_point(rings.front(), {0.5, 0.5}));
  return 0;
}
