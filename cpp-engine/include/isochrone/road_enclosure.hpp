#pragma once

#include <cstddef>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

struct RoadLink {
  std::size_t from{};
  std::size_t to{};
  std::vector<Point> path;
};

struct RoadFace {
  Ring ring;
  std::vector<std::size_t> link_indices;
};

// Find bounded faces made only from explicitly connected road links.
// Geometric crossings without a shared node never create a closure.
[[nodiscard]] std::vector<RoadFace> closed_road_faces(
    const std::vector<RoadLink>& links, std::size_t node_count);

}  // namespace isochrone
