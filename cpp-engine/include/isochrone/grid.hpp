#pragma once

#include "isochrone/types.hpp"

namespace isochrone {

[[nodiscard]] Grid create_grid(const Bounds& bounds, double step_meters);

}  // namespace isochrone
