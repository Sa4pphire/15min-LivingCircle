#include "isochrone/idw.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace isochrone {
namespace {

using DistanceSample = std::pair<double, const Sample*>;

double interpolate_point(const Point& point, const std::vector<Sample>& samples,
                         const IdwOptions& options) {
  std::vector<DistanceSample> distances;
  distances.reserve(samples.size());

  for (const auto& sample : samples) {
    if (!std::isfinite(sample.duration_seconds) ||
        sample.duration_seconds < 0.0) {
      continue;
    }
    const double dx = point.x - sample.point.x;
    const double dy = point.y - sample.point.y;
    const double distance = std::hypot(dx, dy);
    if (distance <= options.exact_match_tolerance_meters) {
      return sample.duration_seconds;
    }
    distances.emplace_back(distance, &sample);
  }

  if (distances.empty()) {
    return std::numeric_limits<double>::quiet_NaN();
  }

  std::sort(distances.begin(), distances.end(),
            [](const auto& left, const auto& right) {
              return left.first < right.first;
            });

  std::vector<DistanceSample> selected;
  for (const auto& item : distances) {
    if (item.first <= options.search_radius_meters) {
      selected.push_back(item);
    }
  }
  if (selected.size() < options.minimum_neighbors) {
    const std::size_t count =
        std::min(options.fallback_neighbors, distances.size());
    selected.assign(distances.begin(), distances.begin() + count);
  }

  double weighted_sum = 0.0;
  double weight_sum = 0.0;
  for (const auto& [distance, sample] : selected) {
    const double weight = 1.0 / std::pow(std::max(distance, 1.0), options.power);
    weighted_sum += weight * sample->duration_seconds;
    weight_sum += weight;
  }
  return weight_sum > 0.0
             ? weighted_sum / weight_sum
             : std::numeric_limits<double>::quiet_NaN();
}

}  // namespace

void interpolate_idw(const std::vector<Sample>& samples, Grid& grid,
                     const IdwOptions& options) {
  if (samples.empty()) {
    throw std::invalid_argument("IDW requires at least one sample");
  }
  if (options.power <= 0.0) {
    throw std::invalid_argument("IDW power must be positive");
  }

  for (std::size_t row = 0; row < grid.rows; ++row) {
    for (std::size_t column = 0; column < grid.columns; ++column) {
      grid.at(row, column) =
          interpolate_point(grid.point_at(row, column), samples, options);
    }
  }
}

}  // namespace isochrone
