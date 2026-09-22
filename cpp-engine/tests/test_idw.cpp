#include <cassert>
#include <cmath>
#include <vector>

#include "isochrone/grid.hpp"
#include "isochrone/idw.hpp"

int main() {
  const std::vector<isochrone::Sample> samples{
      {{0.0, 0.0}, 0.0, 0.0},
      {{100.0, 0.0}, 100.0, 100.0},
  };
  auto grid = isochrone::create_grid({0.0, 100.0, 0.0, 100.0}, 100.0);
  isochrone::interpolate_idw(samples, grid);

  assert(std::abs(grid.at(0, 0) - 0.0) < 1e-9);
  assert(std::abs(grid.at(0, 1) - 100.0) < 1e-9);
  assert(std::isfinite(grid.at(1, 0)));
  return 0;
}
