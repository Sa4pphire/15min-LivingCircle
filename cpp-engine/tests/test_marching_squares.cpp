#include <cassert>

#include "isochrone/grid.hpp"
#include "isochrone/marching_squares.hpp"

int main() {
  auto grid = isochrone::create_grid({0.0, 1.0, 0.0, 1.0}, 1.0);
  grid.at(0, 0) = 0.0;
  grid.at(0, 1) = 2.0;
  grid.at(1, 0) = 2.0;
  grid.at(1, 1) = 2.0;

  const auto segments = isochrone::extract_contour_segments(grid, 1.0);
  assert(segments.size() == 1);
  return 0;
}
