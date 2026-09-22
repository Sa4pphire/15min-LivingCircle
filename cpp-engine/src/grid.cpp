#include "isochrone/grid.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

namespace isochrone {

Grid create_grid(const Bounds& bounds, const double step_meters) {
  if (!std::isfinite(step_meters) || step_meters <= 0.0) {
    throw std::invalid_argument("grid step must be finite and positive");
  }
  if (!(bounds.max_x > bounds.min_x) || !(bounds.max_y > bounds.min_y)) {
    throw std::invalid_argument("grid bounds must have positive area");
  }

  const auto columns = static_cast<std::size_t>(
                           std::floor((bounds.max_x - bounds.min_x) /
                                      step_meters)) +
                       1U;
  const auto rows = static_cast<std::size_t>(
                        std::floor((bounds.max_y - bounds.min_y) /
                                   step_meters)) +
                    1U;

  Grid grid{
      .bounds = bounds,
      .step_meters = step_meters,
      .rows = rows,
      .columns = columns,
      .values = {},
  };
  grid.values.assign(rows * columns,
                     std::numeric_limits<double>::quiet_NaN());
  return grid;
}

}  // namespace isochrone
