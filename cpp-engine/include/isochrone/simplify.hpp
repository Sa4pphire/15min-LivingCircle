#pragma once

#include "isochrone/types.hpp"

namespace isochrone {

[[nodiscard]] Ring simplify_ring(const Ring& ring, double tolerance_meters);

}  // namespace isochrone
