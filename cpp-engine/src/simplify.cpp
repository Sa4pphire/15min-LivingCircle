#include "isochrone/simplify.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

namespace isochrone {
namespace {

double point_segment_distance(const Point& point, const Point& first,
                              const Point& second) {
  const double dx = second.x - first.x;
  const double dy = second.y - first.y;
  const double length_squared = dx * dx + dy * dy;
  if (length_squared == 0.0) {
    return std::hypot(point.x - first.x, point.y - first.y);
  }
  const double projection = std::clamp(
      ((point.x - first.x) * dx + (point.y - first.y) * dy) /
          length_squared,
      0.0, 1.0);
  const Point nearest{first.x + projection * dx, first.y + projection * dy};
  return std::hypot(point.x - nearest.x, point.y - nearest.y);
}

void simplify_range(const std::vector<Point>& points, const std::size_t first,
                    const std::size_t last, const double tolerance,
                    std::vector<bool>& keep) {
  double maximum_distance = 0.0;
  std::size_t maximum_index = first;
  for (std::size_t index = first + 1; index < last; ++index) {
    const double distance = point_segment_distance(
        points[index], points[first], points[last]);
    if (distance > maximum_distance) {
      maximum_distance = distance;
      maximum_index = index;
    }
  }
  if (maximum_distance > tolerance) {
    keep[maximum_index] = true;
    simplify_range(points, first, maximum_index, tolerance, keep);
    simplify_range(points, maximum_index, last, tolerance, keep);
  }
}

}  // namespace

Ring simplify_ring(const Ring& ring, const double tolerance_meters) {
  if (ring.size() <= 4 || tolerance_meters <= 0.0) {
    return ring;
  }

  std::vector<Point> open_ring(ring.begin(), ring.end() - 1);
  std::vector<bool> keep(open_ring.size(), false);
  keep.front() = true;
  keep.back() = true;
  simplify_range(open_ring, 0, open_ring.size() - 1, tolerance_meters, keep);

  Ring result;
  for (std::size_t index = 0; index < open_ring.size(); ++index) {
    if (keep[index]) {
      result.push_back(open_ring[index]);
    }
  }
  if (result.size() < 3) {
    return ring;
  }
  result.push_back(result.front());
  return result;
}

}  // namespace isochrone
