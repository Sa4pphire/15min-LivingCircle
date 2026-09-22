#include "isochrone/marching_squares.hpp"

#include <array>
#include <cmath>

namespace isochrone {
namespace {

Point interpolate_edge(const Point& first, const double first_value,
                       const Point& second, const double second_value,
                       const double threshold) {
  const double denominator = second_value - first_value;
  const double ratio = std::abs(denominator) < 1e-12
                           ? 0.5
                           : (threshold - first_value) / denominator;
  return Point{
      first.x + ratio * (second.x - first.x),
      first.y + ratio * (second.y - first.y),
  };
}

void add_segment(std::vector<Segment>& segments,
                 const std::array<Point, 4>& edges, const int first_edge,
                 const int second_edge) {
  segments.push_back(Segment{edges[static_cast<std::size_t>(first_edge)],
                             edges[static_cast<std::size_t>(second_edge)]});
}

}  // namespace

std::vector<Segment> extract_contour_segments(const Grid& grid,
                                              const double threshold) {
  std::vector<Segment> segments;
  if (grid.rows < 2 || grid.columns < 2) {
    return segments;
  }

  for (std::size_t row = 0; row + 1 < grid.rows; ++row) {
    for (std::size_t column = 0; column + 1 < grid.columns; ++column) {
      const std::array<Point, 4> corners{
          grid.point_at(row, column),
          grid.point_at(row, column + 1),
          grid.point_at(row + 1, column + 1),
          grid.point_at(row + 1, column),
      };
      const std::array<double, 4> values{
          grid.at(row, column),
          grid.at(row, column + 1),
          grid.at(row + 1, column + 1),
          grid.at(row + 1, column),
      };

      bool finite = true;
      for (const double value : values) {
        finite = finite && std::isfinite(value);
      }
      if (!finite) {
        continue;
      }

      int mask = 0;
      for (int index = 0; index < 4; ++index) {
        if (values[static_cast<std::size_t>(index)] <= threshold) {
          mask |= 1 << index;
        }
      }
      if (mask == 0 || mask == 15) {
        continue;
      }

      const std::array<Point, 4> edges{
          interpolate_edge(corners[0], values[0], corners[1], values[1],
                           threshold),
          interpolate_edge(corners[1], values[1], corners[2], values[2],
                           threshold),
          interpolate_edge(corners[2], values[2], corners[3], values[3],
                           threshold),
          interpolate_edge(corners[3], values[3], corners[0], values[0],
                           threshold),
      };

      switch (mask) {
        case 1:
        case 14:
          add_segment(segments, edges, 3, 0);
          break;
        case 2:
        case 13:
          add_segment(segments, edges, 0, 1);
          break;
        case 3:
        case 12:
          add_segment(segments, edges, 3, 1);
          break;
        case 4:
        case 11:
          add_segment(segments, edges, 1, 2);
          break;
        case 6:
        case 9:
          add_segment(segments, edges, 0, 2);
          break;
        case 7:
        case 8:
          add_segment(segments, edges, 3, 2);
          break;
        case 5: {
          const double center =
              (values[0] + values[1] + values[2] + values[3]) / 4.0;
          if (center <= threshold) {
            add_segment(segments, edges, 0, 1);
            add_segment(segments, edges, 2, 3);
          } else {
            add_segment(segments, edges, 3, 0);
            add_segment(segments, edges, 1, 2);
          }
          break;
        }
        case 10: {
          const double center =
              (values[0] + values[1] + values[2] + values[3]) / 4.0;
          if (center <= threshold) {
            add_segment(segments, edges, 3, 0);
            add_segment(segments, edges, 1, 2);
          } else {
            add_segment(segments, edges, 0, 1);
            add_segment(segments, edges, 2, 3);
          }
          break;
        }
        default:
          break;
      }
    }
  }
  return segments;
}

}  // namespace isochrone
