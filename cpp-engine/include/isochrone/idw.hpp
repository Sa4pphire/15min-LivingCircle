#pragma once

#include <cstddef>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

struct IdwOptions {
  double search_radius_meters{350.0};
  std::size_t minimum_neighbors{3};
  std::size_t fallback_neighbors{8};
  double power{2.0};
  double exact_match_tolerance_meters{1.0};
};

void interpolate_idw(const std::vector<Sample>& samples, Grid& grid,
                     const IdwOptions& options = {});

}  // namespace isochrone
