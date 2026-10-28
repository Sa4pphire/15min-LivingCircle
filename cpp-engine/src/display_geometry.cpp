#include "detail/display_geometry.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>

#include "detail/path_geometry.hpp"
#include "isochrone/grid.hpp"
#include "isochrone/marching_squares.hpp"
#include "isochrone/ring_builder.hpp"
#include "isochrone/road_enclosure.hpp"

namespace isochrone::detail {

namespace {

Bounds display_bounds(const std::vector<ReachableEdge>& reachable,
                      double padding, const ExecutionControl& control) {
  Bounds bounds{std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity()};
  for (const auto& edge : reachable) {
    control.Poll();
    for (const Point point : edge.path) {
      control.Poll();
      bounds.min_x = std::min(bounds.min_x, point.x);
      bounds.max_x = std::max(bounds.max_x, point.x);
      bounds.min_y = std::min(bounds.min_y, point.y);
      bounds.max_y = std::max(bounds.max_y, point.y);
    }
  }
  bounds.min_x -= padding;
  bounds.max_x += padding;
  bounds.min_y -= padding;
  bounds.max_y += padding;
  return bounds;
}

Grid display_grid(const std::vector<ReachableEdge>& reachable,
                  double padding, double grid_step,
                  const ExecutionControl& control) {
  const Bounds bounds = display_bounds(reachable, padding, control);
  if ((bounds.max_x - bounds.min_x) / grid_step > 1000.0 ||
      (bounds.max_y - bounds.min_y) / grid_step > 1000.0) {
    throw std::invalid_argument("display grid exceeds 1000 by 1000 cells");
  }
  control.Checkpoint();
  Grid grid = create_grid(bounds, grid_step);
  control.Checkpoint();
  return grid;
}

std::vector<DisplayPolygon> polygons_from_grid(const Grid& grid,
                                               double threshold,
                                               double grid_step,
                                               const ExecutionControl& control) {
  std::vector<Ring> rings = build_rings(
      extract_contour_segments(grid, threshold, control), grid_step * 0.01, control);
  rings.erase(std::remove_if(rings.begin(), rings.end(), [&](const Ring& ring) {
                control.Poll();
                return std::abs(signed_area(ring, control)) < 1.0;
              }), rings.end());
  // Nested rings alternate between exterior, hole and island.
  control.Checkpoint();
  std::vector<int> parents(rings.size(), -1);
  std::vector<double> areas;
  areas.reserve(rings.size());
  control.Checkpoint();
  for (const Ring& ring : rings) {
    control.Poll();
    areas.push_back(std::abs(signed_area(ring, control)));
  }
  for (std::size_t i = 0; i < rings.size(); ++i) {
    control.Poll();
    double parent_area = std::numeric_limits<double>::infinity();
    for (std::size_t j = 0; j < rings.size(); ++j) {
      control.Poll();
      if (areas[j] <= areas[i] + kEpsilon || areas[j] >= parent_area ||
          !contains_point(rings[j], rings[i].front(), control)) {
        continue;
      }
      parents[i] = static_cast<int>(j);
      parent_area = areas[j];
    }
  }
  control.Checkpoint();
  std::vector<int> depths(rings.size());
  control.Checkpoint();
  for (std::size_t i = 0; i < rings.size(); ++i) {
    control.Poll();
    int ancestor = parents[i];
    while (ancestor >= 0) {
      control.Poll();
      ++depths[i];
      ancestor = parents[static_cast<std::size_t>(ancestor)];
    }
  }
  std::vector<DisplayPolygon> polygons;
  control.Checkpoint();
  std::vector<std::size_t> polygon_indices(rings.size());
  control.Checkpoint();
  for (std::size_t i = 0; i < rings.size(); ++i) {
    control.Poll();
    if (depths[i] % 2 != 0) continue;
    Ring outer = std::move(rings[i]);
    if (signed_area(outer, control) < 0.0) {
      control.Checkpoint();
      std::reverse(outer.begin(), outer.end());
      control.Checkpoint();
    }
    polygon_indices[i] = polygons.size();
    polygons.push_back({std::move(outer), {}});
  }
  for (std::size_t i = 0; i < rings.size(); ++i) {
    control.Poll();
    if (depths[i] % 2 == 0) continue;
    Ring hole = std::move(rings[i]);
    if (signed_area(hole, control) > 0.0) {
      control.Checkpoint();
      std::reverse(hole.begin(), hole.end());
      control.Checkpoint();
    }
    polygons[polygon_indices[static_cast<std::size_t>(parents[i])]]
        .holes.push_back(std::move(hole));
  }
  return polygons;
}

}  // namespace

std::vector<DisplayPolygon> display_polygons(
    const std::vector<ReachableEdge>& reachable, double buffer,
    double grid_step, const ExecutionControl& control) {
  if (reachable.empty()) return {};
  double max_buffer = buffer;
  for (const auto& edge : reachable) {
    control.Poll();
    max_buffer = std::max(max_buffer, edge.width_meters / 2.0);
  }
  Grid grid = display_grid(reachable, max_buffer + grid_step * 3.0,
                           grid_step, control);
  const Bounds bounds = grid.bounds;
  control.Checkpoint();
  std::fill(grid.values.begin(), grid.values.end(), max_buffer + grid_step);
  control.Checkpoint();
  for (const auto& edge : reachable) {
    control.Poll();
    const double radius = std::max(buffer, edge.width_meters / 2.0);
    for (std::size_t i = 1; i < edge.path.size(); ++i) {
      control.Poll();
      const Point a = edge.path[i - 1];
      const Point b = edge.path[i];
      const double reach = radius + grid_step;
      const auto min_col = static_cast<std::size_t>(std::max(
          0.0, std::floor((std::min(a.x, b.x) - reach - bounds.min_x) /
                          grid_step)));
      const auto max_col = static_cast<std::size_t>(std::min(
          static_cast<double>(grid.columns - 1),
          std::ceil((std::max(a.x, b.x) + reach - bounds.min_x) / grid_step)));
      const auto min_row = static_cast<std::size_t>(std::max(
          0.0, std::floor((std::min(a.y, b.y) - reach - bounds.min_y) /
                          grid_step)));
      const auto max_row = static_cast<std::size_t>(std::min(
          static_cast<double>(grid.rows - 1),
          std::ceil((std::max(a.y, b.y) + reach - bounds.min_y) / grid_step)));
      for (std::size_t row = min_row; row <= max_row; ++row) {
        control.Poll();
        for (std::size_t col = min_col; col <= max_col; ++col) {
          control.Poll();
          const double candidate = segment_distance(grid.point_at(row, col), a, b)
                                   - (radius - buffer);
          grid.at(row, col) = std::min(grid.at(row, col), candidate);
        }
      }
    }
  }
  return polygons_from_grid(grid, buffer, grid_step, control);
}


namespace {

bool has_opposing_approaches(std::uint8_t directions) {
  for (int sector = 0; sector < 8; ++sector) {
    if ((directions & (1U << sector)) == 0) continue;
    for (int separation = 3; separation <= 5; ++separation) {
      if (directions & (1U << ((sector + separation) % 8))) return true;
    }
  }
  return false;
}

double time_from_street_to_point(const InternalEdge& edge,
                                 const std::vector<double>& arrival,
                                 Point point, double speed,
                                 const ExecutionControl& control) {
  double best = std::numeric_limits<double>::infinity();
  double walked = 0.0;
  for (std::size_t i = 1; i < edge.path.size(); ++i) {
    control.Poll();
    const Point a = edge.path[i - 1];
    const Point b = edge.path[i];
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double squared_length = dx * dx + dy * dy;
    const double length = std::sqrt(squared_length);
    const double fraction = squared_length > 0.0
        ? std::clamp(((point.x - a.x) * dx + (point.y - a.y) * dy) /
                         squared_length, 0.0, 1.0)
        : 0.0;
    const double offset = walked + fraction * length;
    const double along = std::min(
        arrival[edge.from] + offset / speed,
        arrival[edge.to] + (edge.length - offset) / speed);
    best = std::min(best,
        along + distance(point, interpolate(a, b, fraction)) / speed);
    walked += length;
  }
  return best;
}


RoadClosureFillStats fill_closed_road_faces(
    Grid& grid, const std::vector<TimedDisplayEdge>& timed_edges,
    const std::vector<double>& arrival, double threshold, double speed,
    double max_span, const std::vector<double>& bridge_times,
    const ExecutionControl& control) {
  std::vector<RoadLink> links;
  std::vector<const InternalEdge*> perimeter_edges;
  for (const TimedDisplayEdge& timed : timed_edges) {
    control.Poll();
    const InternalEdge& edge = *timed.edge;
    if (timed.interval.from > kEpsilon ||
        timed.interval.to < edge.length - kEpsilon) continue;
    links.push_back({edge.from, edge.to, edge.path});
    perimeter_edges.push_back(&edge);
  }
  RoadClosureFillStats stats;
  const auto faces = closed_road_faces(links, arrival.size(), control);
  stats.face_count = faces.size();
  for (const RoadFace& face : faces) {
    control.Poll();
    Bounds bounds{std::numeric_limits<double>::infinity(),
                  -std::numeric_limits<double>::infinity(),
                  std::numeric_limits<double>::infinity(),
                  -std::numeric_limits<double>::infinity()};
    for (const Point point : face.ring) {
      control.Poll();
      bounds.min_x = std::min(bounds.min_x, point.x);
      bounds.max_x = std::max(bounds.max_x, point.x);
      bounds.min_y = std::min(bounds.min_y, point.y);
      bounds.max_y = std::max(bounds.max_y, point.y);
    }
    // A road cycle confirms topology, not unrestricted access to an entire
    // large parcel. Keep the same modest block-scale visual limit.
    if (bounds.max_x - bounds.min_x > max_span ||
        bounds.max_y - bounds.min_y > max_span) continue;
    const std::size_t min_col = static_cast<std::size_t>(std::max(0.0,
        std::ceil((bounds.min_x - grid.bounds.min_x) / grid.step_meters)));
    const std::size_t max_col = static_cast<std::size_t>(std::min(
        static_cast<double>(grid.columns - 1),
        std::floor((bounds.max_x - grid.bounds.min_x) / grid.step_meters)));
    const std::size_t min_row = static_cast<std::size_t>(std::max(0.0,
        std::ceil((bounds.min_y - grid.bounds.min_y) / grid.step_meters)));
    const std::size_t max_row = static_cast<std::size_t>(std::min(
        static_cast<double>(grid.rows - 1),
        std::floor((bounds.max_y - grid.bounds.min_y) / grid.step_meters)));
    if (min_col > max_col || min_row > max_row) continue;
    for (std::size_t row = min_row; row <= max_row; ++row) {
      control.Poll();
      for (std::size_t col = min_col; col <= max_col; ++col) {
        control.Poll();
        const std::size_t index = row * grid.columns + col;
        if (grid.values[index] <= threshold) continue;
        const Point point = grid.point_at(row, col);
        if (!contains_point(face.ring, point, control)) continue;
        double time = bridge_times[index];
        if (time > threshold) {
          for (const std::size_t link_index : face.link_indices) {
            control.Poll();
            const InternalEdge& edge = *perimeter_edges[link_index];
            if (edge.kind != EdgeKind::walkway) continue;
            time = std::min(time, time_from_street_to_point(
                edge, arrival, point, speed, control));
            if (time <= threshold) break;
          }
        }
        if (time <= threshold) {
          grid.values[index] = time;
          ++stats.filled_cell_count;
        }
      }
    }
  }
  return stats;
}

std::vector<DisplayPolygon> fill_enclosed_blocks(
    std::vector<DisplayPolygon> polygons, double max_span,
    const Grid& grid, const std::vector<double>& approach_times,
    double threshold, double min_hole_area,
    const ExecutionControl& control) {
  for (auto& polygon : polygons) {
    control.Poll();
    polygon.holes.erase(std::remove_if(polygon.holes.begin(),
        polygon.holes.end(), [&](const Ring& hole) {
          control.Poll();
          // Tiny contour artefacts are not meaningful at display resolution.
          if (std::abs(signed_area(hole, control)) < min_hole_area) return true;
          Bounds bounds{std::numeric_limits<double>::infinity(),
                        -std::numeric_limits<double>::infinity(),
                        std::numeric_limits<double>::infinity(),
                        -std::numeric_limits<double>::infinity()};
          for (const Point point : hole) {
            control.Poll();
            bounds.min_x = std::min(bounds.min_x, point.x);
            bounds.max_x = std::max(bounds.max_x, point.x);
            bounds.min_y = std::min(bounds.min_y, point.y);
            bounds.max_y = std::max(bounds.max_y, point.y);
          }
          if (bounds.max_x - bounds.min_x > max_span ||
              bounds.max_y - bounds.min_y > max_span) return false;
          for (std::size_t row = 0; row < grid.rows; ++row) {
            control.Poll();
            const double y = grid.bounds.min_y + row * grid.step_meters;
            if (y < bounds.min_y || y > bounds.max_y) continue;
            for (std::size_t col = 0; col < grid.columns; ++col) {
              control.Poll();
              const double x = grid.bounds.min_x + col * grid.step_meters;
              if (x >= bounds.min_x && x <= bounds.max_x &&
                  contains_point(hole, {x, y}, control) &&
                  approach_times[row * grid.columns + col] > threshold) {
                return false;
              }
            }
          }
          return true;
        }), polygon.holes.end());
  }
  // An island inside a filled hole is now part of its enclosing exterior.
  std::vector<DisplayPolygon> result;
  for (std::size_t i = 0; i < polygons.size(); ++i) {
    control.Poll();
    bool redundant = false;
    for (std::size_t j = 0; j < polygons.size(); ++j) {
      control.Poll();
      if (i == j || std::abs(signed_area(polygons[j].outer, control)) <=
                        std::abs(signed_area(polygons[i].outer, control)) ||
          !contains_point(polygons[j].outer, polygons[i].outer.front(), control)) {
        continue;
      }
      bool in_retained_hole = false;
      for (const Ring& hole : polygons[j].holes) {
        control.Poll();
        in_retained_hole = in_retained_hole ||
            contains_point(hole, polygons[i].outer.front(), control);
      }
      if (!in_retained_hole) redundant = true;
    }
    if (!redundant) result.push_back(std::move(polygons[i]));
  }
  return result;
}

}  // namespace

std::vector<DisplayPolygon> isochrone_polygons(
    const std::vector<ReachableEdge>& reachable,
    const std::vector<TimedDisplayEdge>& timed_edges,
    const std::vector<double>& arrival, double threshold, double speed,
    double area_radius, double connector_radius, double grid_step,
    double min_hole_area, RoadClosureFillStats& closure_stats,
    const ExecutionControl& control) {
  if (reachable.empty()) return {};
  // The regular margin stays small. A wider search is only used where
  // reachable streets bracket an unmarked interior from opposing directions.
  const double bridge_radius = area_radius * 3.0;
  double max_radius = std::max(bridge_radius, connector_radius);
  for (const auto& edge : reachable) {
    control.Poll();
    max_radius = std::max(max_radius, edge.width_meters / 2.0);
  }
  Grid grid = display_grid(reachable, max_radius + grid_step * 3.0,
                           grid_step, control);
  const Bounds bounds = grid.bounds;
  control.Checkpoint();
  std::fill(grid.values.begin(), grid.values.end(),
            threshold + max_radius / speed + grid_step / speed);
  std::vector<std::uint8_t> approaches(grid.values.size(), 0);
  std::vector<double> bridge_times(grid.values.size(),
                                   std::numeric_limits<double>::infinity());
  control.Checkpoint();

  for (const TimedDisplayEdge& timed : timed_edges) {
    control.Poll();
    const InternalEdge& edge = *timed.edge;
    const bool street = edge.kind == EdgeKind::walkway;
    const double radius = std::max(
        street ? area_radius : connector_radius,
        edge.width_meters / 2.0);
    const double search_radius = street ? std::max(radius, bridge_radius)
                                        : radius;
    const std::vector<Point> path = slice(
        edge.path, timed.interval.from, timed.interval.to, control);
    double walked = timed.interval.from;
    for (std::size_t i = 1; i < path.size(); ++i) {
      control.Poll();
      const Point a = path[i - 1];
      const Point b = path[i];
      const double dx = b.x - a.x;
      const double dy = b.y - a.y;
      const double length_squared = dx * dx + dy * dy;
      const double length = std::sqrt(length_squared);
      const double reach = search_radius + grid_step;
      const auto min_col = static_cast<std::size_t>(std::max(
          0.0, std::floor((std::min(a.x, b.x) - reach - bounds.min_x) /
                          grid_step)));
      const auto max_col = static_cast<std::size_t>(std::min(
          static_cast<double>(grid.columns - 1),
          std::ceil((std::max(a.x, b.x) + reach - bounds.min_x) / grid_step)));
      const auto min_row = static_cast<std::size_t>(std::max(
          0.0, std::floor((std::min(a.y, b.y) - reach - bounds.min_y) /
                          grid_step)));
      const auto max_row = static_cast<std::size_t>(std::min(
          static_cast<double>(grid.rows - 1),
          std::ceil((std::max(a.y, b.y) + reach - bounds.min_y) / grid_step)));
      for (std::size_t row = min_row; row <= max_row; ++row) {
        control.Poll();
        for (std::size_t col = min_col; col <= max_col; ++col) {
          control.Poll();
          const Point cell = grid.point_at(row, col);
          const double fraction = length_squared > 0.0
              ? std::clamp(((cell.x - a.x) * dx + (cell.y - a.y) * dy) /
                               length_squared, 0.0, 1.0)
              : 0.0;
          const Point projected = interpolate(a, b, fraction);
          const double lateral = distance(cell, projected);
          if (lateral > search_radius) continue;
          const double offset = walked + fraction * length;
          const double along = std::min(
              arrival[edge.from] + offset / speed,
              arrival[edge.to] + (edge.length - offset) / speed);
          const double wait = edge.kind == EdgeKind::crossing
              ? edge.wait_seconds : 0.0;
          const double candidate_time = along + wait + lateral / speed;
          if (lateral <= radius) {
            grid.at(row, col) = std::min(grid.at(row, col), candidate_time);
          } else if (street && candidate_time <= threshold) {
            constexpr double kPi = 3.14159265358979323846;
            const double angle = std::atan2(projected.y - cell.y,
                                            projected.x - cell.x);
            const int sector = (static_cast<int>(std::floor(
                (angle + kPi / 8.0) / (kPi / 4.0))) + 8) % 8;
            const std::size_t index = row * grid.columns + col;
            approaches[index] |= static_cast<std::uint8_t>(1U << sector);
            bridge_times[index] = std::min(bridge_times[index], candidate_time);
          }
        }
      }
      walked += length;
    }
  }
  for (std::size_t index = 0; index < grid.values.size(); ++index) {
    control.Poll();
    if (grid.values[index] > threshold &&
        has_opposing_approaches(approaches[index])) {
      grid.values[index] = bridge_times[index];
    }
  }
  closure_stats = fill_closed_road_faces(
      grid, timed_edges, arrival, threshold, speed,
      std::max(480.0, bridge_radius * 2.0), bridge_times, control);
  return fill_enclosed_blocks(
      polygons_from_grid(grid, threshold, grid_step, control), bridge_radius * 2.0,
      grid, bridge_times, threshold, min_hole_area, control);
}

}  // namespace isochrone::detail
