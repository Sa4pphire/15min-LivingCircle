#include <cassert>
#include <cmath>
#include <vector>

#include "isochrone/ring_builder.hpp"

int main() {
  const std::vector<isochrone::Segment> segments{
      {{0.0, 0.0}, {1.0, 0.0}},
      {{1.0, 1.0}, {0.0, 1.0}},
      {{1.0, 0.0}, {1.0, 1.0}},
      {{0.0, 1.0}, {0.0, 0.0}},
  };

  const auto rings = isochrone::build_rings(segments);
  assert(rings.size() == 1);
  assert(std::abs(isochrone::signed_area(rings.front())) == 1.0);
  assert(isochrone::contains_point(rings.front(), {0.5, 0.5}));
  return 0;
}
