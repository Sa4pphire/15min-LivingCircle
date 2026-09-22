#pragma once

#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

[[nodiscard]] std::vector<Segment> extract_contour_segments(
    const Grid& grid, double threshold);

}  // namespace isochrone
