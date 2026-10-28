#pragma once

#include <optional>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

class ExecutionControl;

[[nodiscard]] std::vector<Ring> build_rings(
    const std::vector<Segment>& segments, double endpoint_tolerance_meters = 1.0);
[[nodiscard]] std::vector<Ring> build_rings(
    const std::vector<Segment>& segments, double endpoint_tolerance_meters,
    const ExecutionControl& control);

[[nodiscard]] double signed_area(const Ring& ring);
[[nodiscard]] double signed_area(const Ring& ring, const ExecutionControl& control);
[[nodiscard]] bool contains_point(const Ring& ring, const Point& point);
[[nodiscard]] bool contains_point(const Ring& ring, const Point& point,
                                  const ExecutionControl& control);
[[nodiscard]] std::optional<Ring> select_primary_ring(
    const std::vector<Ring>& rings, const Point& center = {});

}  // namespace isochrone
