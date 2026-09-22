#pragma once

#include <cstddef>
#include <stdexcept>
#include <vector>

namespace isochrone {

struct Point {
  double x{};
  double y{};
};

struct Sample {
  Point point;
  double duration_seconds{};
  double distance_meters{};
};

struct Bounds {
  double min_x{};
  double max_x{};
  double min_y{};
  double max_y{};
};

struct Segment {
  Point first;
  Point second;
};

using Ring = std::vector<Point>;

struct Grid {
  Bounds bounds;
  double step_meters{};
  std::size_t rows{};
  std::size_t columns{};
  std::vector<double> values;

  [[nodiscard]] double& at(std::size_t row, std::size_t column) {
    if (row >= rows || column >= columns) {
      throw std::out_of_range("grid index out of range");
    }
    return values[row * columns + column];
  }

  [[nodiscard]] double at(std::size_t row, std::size_t column) const {
    if (row >= rows || column >= columns) {
      throw std::out_of_range("grid index out of range");
    }
    return values[row * columns + column];
  }

  [[nodiscard]] Point point_at(std::size_t row, std::size_t column) const {
    return Point{
        bounds.min_x + static_cast<double>(column) * step_meters,
        bounds.min_y + static_cast<double>(row) * step_meters,
    };
  }
};

}  // namespace isochrone
