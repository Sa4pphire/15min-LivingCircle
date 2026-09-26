#include "isochrone/engine.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <queue>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "isochrone/grid.hpp"
#include "isochrone/marching_squares.hpp"
#include "isochrone/road_enclosure.hpp"
#include "isochrone/ring_builder.hpp"

namespace isochrone {
namespace {

constexpr double kEndpointTolerance = 1e-6;
constexpr double kFacilityAccessTolerance = 1.0;
constexpr double kOriginSideAmbiguityMeters = 2.0;
constexpr double kEpsilon = 1e-8;

double distance(Point a, Point b) { return std::hypot(a.x - b.x, a.y - b.y); }

bool finite(Point point) {
  return std::isfinite(point.x) && std::isfinite(point.y);
}

double path_length(const std::vector<Point>& path) {
  double result = 0.0;
  for (std::size_t i = 1; i < path.size(); ++i) {
    result += distance(path[i - 1], path[i]);
  }
  return result;
}

Point interpolate(Point a, Point b, double fraction) {
  return {a.x + (b.x - a.x) * fraction,
          a.y + (b.y - a.y) * fraction};
}

Point point_at(const std::vector<Point>& path, double offset) {
  double walked = 0.0;
  for (std::size_t i = 1; i < path.size(); ++i) {
    const double length = distance(path[i - 1], path[i]);
    if (walked + length >= offset - kEpsilon) {
      return interpolate(path[i - 1], path[i],
                         length > 0.0 ? std::clamp((offset - walked) / length,
                                                   0.0, 1.0)
                                      : 0.0);
    }
    walked += length;
  }
  return path.back();
}

std::vector<Point> slice(const std::vector<Point>& path, double from,
                         double to) {
  const double length = path_length(path);
  from = std::clamp(from, 0.0, length);
  to = std::clamp(to, from, length);
  std::vector<Point> result{point_at(path, from)};
  double walked = 0.0;
  for (std::size_t i = 1; i + 1 < path.size(); ++i) {
    walked += distance(path[i - 1], path[i]);
    if (walked > from + kEpsilon && walked < to - kEpsilon) {
      result.push_back(path[i]);
    }
  }
  const Point end = point_at(path, to);
  if (distance(result.back(), end) > kEpsilon) result.push_back(end);
  return result;
}

struct Snap {
  std::size_t edge_index{};
  Point point;
  double distance_meters{std::numeric_limits<double>::infinity()};
  double surface_distance_meters{std::numeric_limits<double>::infinity()};
  double offset_meters{};
};

Snap project_to_edge(Point point, const WalkEdge& edge,
                     std::size_t edge_index) {
  Snap best;
  best.edge_index = edge_index;
  double walked = 0.0;
  for (std::size_t i = 1; i < edge.path.size(); ++i) {
    const Point a = edge.path[i - 1];
    const Point b = edge.path[i];
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double squared_length = dx * dx + dy * dy;
    const double fraction = squared_length > 0.0
                                ? std::clamp(((point.x - a.x) * dx +
                                              (point.y - a.y) * dy) /
                                                 squared_length,
                                             0.0, 1.0)
                                : 0.0;
    const Point projection = interpolate(a, b, fraction);
    const double candidate_distance = distance(point, projection);
    if (candidate_distance < best.distance_meters) {
      best.point = projection;
      best.distance_meters = candidate_distance;
      best.surface_distance_meters = edge.kind == EdgeKind::shared_way
          ? std::max(0.0, candidate_distance - edge.width_meters / 2.0)
          : candidate_distance;
      best.offset_meters = walked + fraction * std::sqrt(squared_length);
    }
    walked += std::sqrt(squared_length);
  }
  return best;
}

bool share_endpoint(const WalkEdge& first, const WalkEdge& second) {
  return first.from == second.from || first.from == second.to ||
         first.to == second.from || first.to == second.to;
}

Snap snap_origin(const EngineInput& input) {
  std::vector<Snap> candidates;
  for (std::size_t edge_index = 0; edge_index < input.edges.size();
       ++edge_index) {
    const auto& edge = input.edges[edge_index];
    if (edge.kind == EdgeKind::sidewalk || edge.kind == EdgeKind::shared_way) {
      candidates.push_back(project_to_edge(input.origin, edge, edge_index));
    }
  }
  if (candidates.empty()) {
    throw OriginNotOnWalkway("walking graph has no traversable street edge");
  }
  const auto in_range = [&](const Snap& candidate) {
    const WalkEdge& edge = input.edges[candidate.edge_index];
    if (input.allow_off_network_origin) {
      return candidate.distance_meters <= input.max_origin_snap_meters;
    }
    return candidate.surface_distance_meters <=
        (edge.kind == EdgeKind::shared_way ? 3.0 : input.max_origin_snap_meters);
  };
  const auto access_distance = [&](const Snap& candidate) {
    return input.allow_off_network_origin ? candidate.distance_meters
                                          : candidate.surface_distance_meters;
  };
  auto nearest = candidates.end();
  for (auto candidate = candidates.begin(); candidate != candidates.end();
       ++candidate) {
    if (in_range(*candidate) &&
        (nearest == candidates.end() || access_distance(*candidate) <
                                           access_distance(*nearest))) {
      nearest = candidate;
    }
  }
  if (nearest == candidates.end()) {
    throw OriginNotOnWalkway("origin is outside the supported walkway snap range");
  }
  if (input.origin_edge_id) {
    const auto selected = std::find_if(
        candidates.begin(), candidates.end(), [&](const Snap& snap) {
          return input.edges[snap.edge_index].id == *input.origin_edge_id;
        });
    if (selected == candidates.end()) {
      throw std::invalid_argument("originEdgeId must identify a sidewalk or shared_way");
    }
    if (!in_range(*selected) ||
        access_distance(*selected) >
            access_distance(*nearest) + kOriginSideAmbiguityMeters) {
      throw OriginNotOnWalkway(
          "originEdgeId is not on the nearest sidewalk side");
    }
    return *selected;
  }
  for (const Snap& candidate : candidates) {
    if (candidate.edge_index != nearest->edge_index &&
        !share_endpoint(input.edges[candidate.edge_index],
                        input.edges[nearest->edge_index]) &&
        in_range(candidate) && access_distance(candidate) <=
            access_distance(*nearest) + kOriginSideAmbiguityMeters) {
      throw AmbiguousOriginSide(
          "multiple sidewalk sides are equally near: " +
          input.edges[nearest->edge_index].id + ", " +
          input.edges[candidate.edge_index].id +
          "; specify originEdgeId");
    }
  }
  return *nearest;
}

struct InternalEdge {
  std::string source_id;
  EdgeKind kind;
  std::size_t from{};
  std::size_t to{};
  std::vector<Point> path;
  double length{};
  double start_offset{};
  double width_meters{};
  double wait_seconds{};
};

struct Interval {
  double from{};
  double to{};
};

struct Arc {
  std::size_t to{};
  double cost{};
};

std::vector<double> shortest_times(
    const std::vector<std::vector<Arc>>& adjacency,
    const std::vector<std::pair<std::size_t, double>>& sources) {
  std::vector<double> times(adjacency.size(),
                            std::numeric_limits<double>::infinity());
  using Item = std::pair<double, std::size_t>;
  std::priority_queue<Item, std::vector<Item>, std::greater<Item>> queue;
  for (const auto& [node, cost] : sources) {
    if (cost < times[node]) {
      times[node] = cost;
      queue.emplace(cost, node);
    }
  }
  while (!queue.empty()) {
    const auto [time, node] = queue.top();
    queue.pop();
    if (time > times[node] + kEpsilon) continue;
    for (const Arc arc : adjacency[node]) {
      if (time + arc.cost < times[arc.to]) {
        times[arc.to] = time + arc.cost;
        queue.emplace(times[arc.to], arc.to);
      }
    }
  }
  return times;
}

std::vector<Interval> reachable_intervals(
    const InternalEdge& edge, const std::vector<double>& times,
    double threshold, double speed) {
  if (edge.kind == EdgeKind::crossing) {
    const double cost = edge.length / speed + edge.wait_seconds;
    if (times[edge.from] + cost <= threshold + kEpsilon ||
        times[edge.to] + cost <= threshold + kEpsilon) {
      return {{0.0, edge.length}};
    }
    return {};
  }
  const auto reach = [&](double time) {
    return std::isfinite(time)
        ? std::clamp((threshold - time) * speed, 0.0, edge.length)
        : 0.0;
  };
  const double front = reach(times[edge.from]);
  const double back = reach(times[edge.to]);
  if (front + back >= edge.length - kEpsilon) {
    return front > kEpsilon || back > kEpsilon
        ? std::vector<Interval>{{0.0, edge.length}}
        : std::vector<Interval>{};
  }
  std::vector<Interval> intervals;
  if (front > kEpsilon) intervals.push_back({0.0, front});
  if (back > kEpsilon) intervals.push_back({edge.length - back, edge.length});
  return intervals;
}

std::vector<Interval> subtract_intervals(
    const std::vector<Interval>& base,
    const std::vector<Interval>& covered) {
  std::vector<Interval> result;
  for (const Interval piece : base) {
    double cursor = piece.from;
    for (const Interval service : covered) {
      if (service.to <= cursor + kEpsilon || service.from >= piece.to - kEpsilon) {
        continue;
      }
      if (service.from > cursor + kEpsilon) {
        result.push_back({cursor, std::min(service.from, piece.to)});
      }
      cursor = std::max(cursor, service.to);
      if (cursor >= piece.to - kEpsilon) break;
    }
    if (cursor < piece.to - kEpsilon) result.push_back({cursor, piece.to});
  }
  return result;
}

double segment_distance(Point point, Point a, Point b) {
  const double dx = b.x - a.x;
  const double dy = b.y - a.y;
  const double length_squared = dx * dx + dy * dy;
  const double fraction = length_squared > 0.0
                              ? std::clamp(((point.x - a.x) * dx +
                                            (point.y - a.y) * dy) /
                                               length_squared,
                                           0.0, 1.0)
                              : 0.0;
  return distance(point, interpolate(a, b, fraction));
}

Bounds display_bounds(const std::vector<ReachableEdge>& reachable,
                      double padding) {
  Bounds bounds{std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity()};
  for (const auto& edge : reachable) {
    for (const Point point : edge.path) {
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
                  double padding, double grid_step) {
  const Bounds bounds = display_bounds(reachable, padding);
  if ((bounds.max_x - bounds.min_x) / grid_step > 1000.0 ||
      (bounds.max_y - bounds.min_y) / grid_step > 1000.0) {
    throw std::invalid_argument("display grid exceeds 1000 by 1000 cells");
  }
  return create_grid(bounds, grid_step);
}

std::vector<DisplayPolygon> polygons_from_grid(const Grid& grid,
                                               double threshold,
                                               double grid_step) {
  std::vector<Ring> rings = build_rings(
      extract_contour_segments(grid, threshold), grid_step * 0.01);
  rings.erase(std::remove_if(rings.begin(), rings.end(), [](const Ring& ring) {
                return std::abs(signed_area(ring)) < 1.0;
              }), rings.end());
  // Nested rings alternate between exterior, hole and island.
  std::vector<int> parents(rings.size(), -1);
  std::vector<double> areas;
  areas.reserve(rings.size());
  for (const Ring& ring : rings) areas.push_back(std::abs(signed_area(ring)));
  for (std::size_t i = 0; i < rings.size(); ++i) {
    double parent_area = std::numeric_limits<double>::infinity();
    for (std::size_t j = 0; j < rings.size(); ++j) {
      if (areas[j] <= areas[i] + kEpsilon || areas[j] >= parent_area ||
          !contains_point(rings[j], rings[i].front())) {
        continue;
      }
      parents[i] = static_cast<int>(j);
      parent_area = areas[j];
    }
  }
  std::vector<int> depths(rings.size());
  for (std::size_t i = 0; i < rings.size(); ++i) {
    int ancestor = parents[i];
    while (ancestor >= 0) {
      ++depths[i];
      ancestor = parents[static_cast<std::size_t>(ancestor)];
    }
  }
  std::vector<DisplayPolygon> polygons;
  std::vector<std::size_t> polygon_indices(rings.size());
  for (std::size_t i = 0; i < rings.size(); ++i) {
    if (depths[i] % 2 != 0) continue;
    Ring outer = std::move(rings[i]);
    if (signed_area(outer) < 0.0) std::reverse(outer.begin(), outer.end());
    polygon_indices[i] = polygons.size();
    polygons.push_back({std::move(outer), {}});
  }
  for (std::size_t i = 0; i < rings.size(); ++i) {
    if (depths[i] % 2 == 0) continue;
    Ring hole = std::move(rings[i]);
    if (signed_area(hole) > 0.0) std::reverse(hole.begin(), hole.end());
    polygons[polygon_indices[static_cast<std::size_t>(parents[i])]]
        .holes.push_back(std::move(hole));
  }
  return polygons;
}

std::vector<DisplayPolygon> display_polygons(
    const std::vector<ReachableEdge>& reachable, double buffer,
    double grid_step) {
  if (reachable.empty()) return {};
  double max_buffer = buffer;
  for (const auto& edge : reachable) {
    max_buffer = std::max(max_buffer, edge.width_meters / 2.0);
  }
  Grid grid = display_grid(reachable, max_buffer + grid_step * 3.0,
                           grid_step);
  const Bounds bounds = grid.bounds;
  std::fill(grid.values.begin(), grid.values.end(), max_buffer + grid_step);
  for (const auto& edge : reachable) {
    const double radius = std::max(buffer, edge.width_meters / 2.0);
    for (std::size_t i = 1; i < edge.path.size(); ++i) {
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
        for (std::size_t col = min_col; col <= max_col; ++col) {
          const double candidate = segment_distance(grid.point_at(row, col), a, b)
                                   - (radius - buffer);
          grid.at(row, col) = std::min(grid.at(row, col), candidate);
        }
      }
    }
  }
  return polygons_from_grid(grid, buffer, grid_step);
}

struct TimedDisplayEdge {
  const InternalEdge* edge;
  Interval interval;
};

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
                                 Point point, double speed) {
  double best = std::numeric_limits<double>::infinity();
  double walked = 0.0;
  for (std::size_t i = 1; i < edge.path.size(); ++i) {
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

struct RoadClosureFillStats {
  std::size_t face_count{};
  std::size_t filled_cell_count{};
};

RoadClosureFillStats fill_closed_road_faces(
    Grid& grid, const std::vector<TimedDisplayEdge>& timed_edges,
    const std::vector<double>& arrival, double threshold, double speed,
    double max_span, const std::vector<double>& bridge_times) {
  std::vector<RoadLink> links;
  std::vector<const InternalEdge*> perimeter_edges;
  for (const TimedDisplayEdge& timed : timed_edges) {
    const InternalEdge& edge = *timed.edge;
    if (timed.interval.from > kEpsilon ||
        timed.interval.to < edge.length - kEpsilon) continue;
    links.push_back({edge.from, edge.to, edge.path});
    perimeter_edges.push_back(&edge);
  }
  RoadClosureFillStats stats;
  const auto faces = closed_road_faces(links, arrival.size());
  stats.face_count = faces.size();
  for (const RoadFace& face : faces) {
    Bounds bounds{std::numeric_limits<double>::infinity(),
                  -std::numeric_limits<double>::infinity(),
                  std::numeric_limits<double>::infinity(),
                  -std::numeric_limits<double>::infinity()};
    for (const Point point : face.ring) {
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
      for (std::size_t col = min_col; col <= max_col; ++col) {
        const std::size_t index = row * grid.columns + col;
        if (grid.values[index] <= threshold) continue;
        const Point point = grid.point_at(row, col);
        if (!contains_point(face.ring, point)) continue;
        double time = bridge_times[index];
        if (time > threshold) {
          for (const std::size_t link_index : face.link_indices) {
            const InternalEdge& edge = *perimeter_edges[link_index];
            if (edge.kind != EdgeKind::sidewalk &&
                edge.kind != EdgeKind::shared_way) continue;
            time = std::min(time, time_from_street_to_point(
                edge, arrival, point, speed));
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
    double threshold, double min_hole_area) {
  for (auto& polygon : polygons) {
    polygon.holes.erase(std::remove_if(polygon.holes.begin(),
        polygon.holes.end(), [&](const Ring& hole) {
          // Tiny contour artefacts are not meaningful at display resolution.
          if (std::abs(signed_area(hole)) < min_hole_area) return true;
          Bounds bounds{std::numeric_limits<double>::infinity(),
                        -std::numeric_limits<double>::infinity(),
                        std::numeric_limits<double>::infinity(),
                        -std::numeric_limits<double>::infinity()};
          for (const Point point : hole) {
            bounds.min_x = std::min(bounds.min_x, point.x);
            bounds.max_x = std::max(bounds.max_x, point.x);
            bounds.min_y = std::min(bounds.min_y, point.y);
            bounds.max_y = std::max(bounds.max_y, point.y);
          }
          if (bounds.max_x - bounds.min_x > max_span ||
              bounds.max_y - bounds.min_y > max_span) return false;
          for (std::size_t row = 0; row < grid.rows; ++row) {
            const double y = grid.bounds.min_y + row * grid.step_meters;
            if (y < bounds.min_y || y > bounds.max_y) continue;
            for (std::size_t col = 0; col < grid.columns; ++col) {
              const double x = grid.bounds.min_x + col * grid.step_meters;
              if (x >= bounds.min_x && x <= bounds.max_x &&
                  contains_point(hole, {x, y}) &&
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
    bool redundant = false;
    for (std::size_t j = 0; j < polygons.size(); ++j) {
      if (i == j || std::abs(signed_area(polygons[j].outer)) <=
                        std::abs(signed_area(polygons[i].outer)) ||
          !contains_point(polygons[j].outer, polygons[i].outer.front())) {
        continue;
      }
      bool in_retained_hole = false;
      for (const Ring& hole : polygons[j].holes) {
        in_retained_hole = in_retained_hole ||
            contains_point(hole, polygons[i].outer.front());
      }
      if (!in_retained_hole) redundant = true;
    }
    if (!redundant) result.push_back(std::move(polygons[i]));
  }
  return result;
}

std::vector<DisplayPolygon> isochrone_polygons(
    const std::vector<ReachableEdge>& reachable,
    const std::vector<TimedDisplayEdge>& timed_edges,
    const std::vector<double>& arrival, double threshold, double speed,
    double area_radius, double connector_radius, double grid_step,
    double min_hole_area, RoadClosureFillStats& closure_stats) {
  if (reachable.empty()) return {};
  // The regular margin stays small. A wider search is only used where
  // reachable streets bracket an unmarked interior from opposing directions.
  const double bridge_radius = area_radius * 3.0;
  double max_radius = std::max(bridge_radius, connector_radius);
  for (const auto& edge : reachable) {
    max_radius = std::max(max_radius, edge.width_meters / 2.0);
  }
  Grid grid = display_grid(reachable, max_radius + grid_step * 3.0,
                           grid_step);
  const Bounds bounds = grid.bounds;
  std::fill(grid.values.begin(), grid.values.end(),
            threshold + max_radius / speed + grid_step / speed);
  std::vector<std::uint8_t> approaches(grid.values.size(), 0);
  std::vector<double> bridge_times(grid.values.size(),
                                   std::numeric_limits<double>::infinity());

  for (const TimedDisplayEdge& timed : timed_edges) {
    const InternalEdge& edge = *timed.edge;
    const bool street = edge.kind == EdgeKind::sidewalk ||
                        edge.kind == EdgeKind::shared_way;
    const double radius = std::max(
        street ? area_radius : connector_radius,
        edge.width_meters / 2.0);
    const double search_radius = street ? std::max(radius, bridge_radius)
                                        : radius;
    const std::vector<Point> path = slice(
        edge.path, timed.interval.from, timed.interval.to);
    double walked = timed.interval.from;
    for (std::size_t i = 1; i < path.size(); ++i) {
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
        for (std::size_t col = min_col; col <= max_col; ++col) {
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
    if (grid.values[index] > threshold &&
        has_opposing_approaches(approaches[index])) {
      grid.values[index] = bridge_times[index];
    }
  }
  closure_stats = fill_closed_road_faces(
      grid, timed_edges, arrival, threshold, speed,
      std::max(480.0, bridge_radius * 2.0), bridge_times);
  return fill_enclosed_blocks(
      polygons_from_grid(grid, threshold, grid_step), bridge_radius * 2.0,
      grid, bridge_times, threshold, min_hole_area);
}

}  // namespace

const char* edge_kind_name(EdgeKind kind) {
  switch (kind) {
    case EdgeKind::sidewalk: return "sidewalk";
    case EdgeKind::shared_way: return "shared_way";
    case EdgeKind::turn: return "turn";
    case EdgeKind::crossing: return "crossing";
  }
  return "unknown";
}

void validate_graph(const EngineInput& input) {
  if (input.schema_version != 2 || !finite(input.origin) ||
      !std::isfinite(input.threshold_seconds) ||
      input.threshold_seconds <= 0.0 ||
      !std::isfinite(input.walking_speed_meters_per_second) ||
      input.walking_speed_meters_per_second <= 0.0 ||
      !std::isfinite(input.crossing_wait_seconds) ||
      input.crossing_wait_seconds < 0.0 ||
      !std::isfinite(input.max_origin_snap_meters) ||
      input.max_origin_snap_meters <= 0.0 ||
      !std::isfinite(input.display_buffer_meters) ||
      input.display_buffer_meters <= 0.0 ||
      !std::isfinite(input.display_area_radius_meters) ||
      input.display_area_radius_meters <= 0.0 ||
      !std::isfinite(input.display_min_hole_area_square_meters) ||
      input.display_min_hole_area_square_meters < 0.0 ||
      !std::isfinite(input.display_grid_step_meters) ||
      input.display_grid_step_meters <= 0.0) {
    throw std::invalid_argument("invalid engine parameters");
  }
  if (input.nodes.empty() || input.edges.empty()) {
    throw std::invalid_argument("walking graph must contain nodes and edges");
  }
  std::unordered_map<std::string, Point> nodes;
  for (const auto& node : input.nodes) {
    if (node.id.empty() || !finite(node.point) ||
        !nodes.emplace(node.id, node.point).second) {
      throw std::invalid_argument("invalid or duplicate walking node");
    }
  }
  std::unordered_set<std::string> edge_ids;
  std::unordered_map<std::string, const WalkEdge*> edge_lookup;
  std::unordered_map<std::string, EdgeKind> block_kinds;
  std::unordered_map<std::string, std::unordered_map<std::string, std::string>>
      block_node_sides;
  std::unordered_map<std::string, std::unordered_map<std::string, std::string>>
      node_block_sides;
  for (const auto& edge : input.edges) {
    if (edge.id.empty() || !edge_ids.insert(edge.id).second ||
        edge.from == edge.to || !nodes.count(edge.from) ||
        !nodes.count(edge.to) || edge.path.size() < 2 ||
        edge.path.size() > 1000) {
      throw std::invalid_argument("invalid walking edge identity or endpoints");
    }
    edge_lookup.emplace(edge.id, &edge);
    for (Point point : edge.path) {
      if (!finite(point)) throw std::invalid_argument("invalid edge coordinate");
    }
    if (distance(edge.path.front(), nodes.at(edge.from)) > kEndpointTolerance ||
        distance(edge.path.back(), nodes.at(edge.to)) > kEndpointTolerance ||
        path_length(edge.path) <= kEpsilon) {
      throw std::invalid_argument("edge path does not match its nodes");
    }
    if (edge.kind == EdgeKind::sidewalk &&
        (edge.street_block_id.empty() ||
         (edge.side != "left" && edge.side != "right"))) {
      throw std::invalid_argument("sidewalk needs streetBlockId and side");
    }
    if (edge.kind == EdgeKind::shared_way &&
        (edge.street_block_id.empty() || !edge.side.empty() ||
         (edge.shared_way_type != "pedestrian_street" &&
          edge.shared_way_type != "shared_alley") ||
         !std::isfinite(edge.width_meters) || edge.width_meters <= 0.0)) {
      throw std::invalid_argument(
          "shared_way needs streetBlockId, sharedWayType and positive widthMeters, without side");
    }
    if (edge.kind != EdgeKind::shared_way &&
        (!edge.shared_way_type.empty() || edge.width_meters != 0.0)) {
      throw std::invalid_argument("only shared_way may declare sharedWayType or widthMeters");
    }
    if (edge.wait_seconds &&
        (edge.kind != EdgeKind::crossing || !std::isfinite(*edge.wait_seconds) ||
         *edge.wait_seconds < 0.0)) {
      throw std::invalid_argument("waitSeconds must be non-negative and only on crossing");
    }
    if (edge.kind == EdgeKind::sidewalk || edge.kind == EdgeKind::shared_way) {
      const auto [found, inserted] = block_kinds.emplace(edge.street_block_id, edge.kind);
      if (!inserted && found->second != edge.kind) {
        throw std::invalid_argument("streetBlockId mixes separated and shared road models");
      }
    }
    if (edge.kind == EdgeKind::sidewalk) {
      for (const auto& node_id : {edge.from, edge.to}) {
        const auto [found, inserted] =
            block_node_sides[edge.street_block_id].emplace(node_id, edge.side);
        if (!inserted && found->second != edge.side) {
          throw std::invalid_argument(
              "opposite sidewalk sides of one streetBlockId share a node");
        }
        node_block_sides[node_id][edge.street_block_id] = edge.side;
      }
    }
  }
  for (const auto& edge : input.edges) {
    if (edge.kind != EdgeKind::turn) continue;
    const auto from = node_block_sides.find(edge.from);
    const auto to = node_block_sides.find(edge.to);
    if (from == node_block_sides.end() || to == node_block_sides.end()) continue;
    for (const auto& [block, side] : from->second) {
      const auto opposite = to->second.find(block);
      if (opposite != to->second.end() && opposite->second != side) {
        throw std::invalid_argument(
            "turn connects opposite sides of one streetBlockId; use crossing");
      }
    }
  }
  std::unordered_set<std::string> category_ids;
  for (const auto& category : input.service_categories) {
    if (category.id.empty() || !category_ids.insert(category.id).second ||
        (category.data_status != "reviewed_online" &&
         category.data_status != "incomplete")) {
      throw std::invalid_argument("invalid or duplicate service category");
    }
  }
  std::unordered_set<std::string> facility_ids;
  for (const auto& facility : input.facilities) {
    if (facility.id.empty() || !facility_ids.insert(facility.id).second ||
        (!facility.category.empty() && !category_ids.count(facility.category))) {
      throw std::invalid_argument("invalid or duplicate facility access");
    }
    if (!facility.entrances.empty() && !facility.access_edge_id.empty()) {
      throw std::invalid_argument("facility cannot mix legacy and entrance fields");
    }
    if (facility.entrances.empty() &&
        (facility.access_edge_id.empty() || !finite(facility.access_point))) {
      throw std::invalid_argument("facility needs an entrance");
    }
    std::unordered_set<std::string> entrance_ids;
    std::vector<FacilityEntrance> legacy;
    if (facility.entrances.empty()) {
      legacy.push_back({facility.id, facility.access_edge_id,
                        facility.access_point, {}});
    }
    const auto& entrances = facility.entrances.empty() ? legacy : facility.entrances;
    for (const auto& entrance : entrances) {
      if (entrance.id.empty() || !entrance_ids.insert(entrance.id).second ||
          !finite(entrance.street_access_point)) {
        throw std::invalid_argument("invalid or duplicate facility entrance");
      }
      const auto edge = edge_lookup.find(entrance.access_edge_id);
      if (edge == edge_lookup.end() ||
          (edge->second->kind != EdgeKind::sidewalk &&
           edge->second->kind != EdgeKind::shared_way)) {
        throw std::invalid_argument("facility accessEdgeId must identify a traversable edge");
      }
      const Snap access = project_to_edge(
          entrance.street_access_point, *edge->second, 0);
      if (access.surface_distance_meters > kFacilityAccessTolerance) {
        throw std::invalid_argument("facility street access is outside its walkable edge");
      }
      if (!entrance.access_path.empty()) {
        if (entrance.access_path.size() < 2 ||
            entrance.access_path.size() > 1000 ||
            distance(entrance.access_path.front(), entrance.street_access_point) >
                kEndpointTolerance ||
            path_length(entrance.access_path) <= kEpsilon) {
          throw std::invalid_argument("invalid facility accessPathMeters");
        }
        for (Point point : entrance.access_path) {
          if (!finite(point)) {
            throw std::invalid_argument("invalid facility access path coordinate");
          }
        }
      }
    }
  }
}

EngineResult compute_reachability(const EngineInput& input) {
  validate_graph(input);
  const Snap snap = snap_origin(input);
  const double access_seconds = snap.distance_meters /
      input.walking_speed_meters_per_second;
  if (access_seconds >= input.threshold_seconds - kEpsilon) {
    throw OriginNotOnWalkway(
        "reaching the nearest walkway uses the entire time budget");
  }
  EngineResult result;
  result.snapped_origin = snap.point;
  result.snap_distance_meters = snap.distance_meters;
  result.origin_access_seconds = access_seconds;
  if (input.allow_off_network_origin && snap.distance_meters > kEpsilon) {
    result.warnings.push_back("UNVERIFIED_STRAIGHT_LINE_ORIGIN_ACCESS");
  }
  if (snap.distance_meters > 10.0) {
    result.warnings.push_back("ORIGIN_SNAP_OVER_10_METERS");
  }

  std::unordered_map<std::string, std::size_t> node_indices;
  std::vector<Point> graph_points;
  for (std::size_t i = 0; i < input.nodes.size(); ++i) {
    node_indices.emplace(input.nodes[i].id, i);
    graph_points.push_back(input.nodes[i].point);
  }
  std::unordered_map<std::string, std::size_t> edge_indices;
  struct Mark { double offset; std::size_t node; };
  std::vector<std::vector<Mark>> marks(input.edges.size());
  for (std::size_t i = 0; i < input.edges.size(); ++i) {
    const WalkEdge& edge = input.edges[i];
    edge_indices.emplace(edge.id, i);
    marks[i].push_back({0.0, node_indices.at(edge.from)});
    marks[i].push_back({path_length(edge.path), node_indices.at(edge.to)});
  }
  const auto register_position = [&](std::size_t edge_index, double offset) {
    for (const Mark mark : marks[edge_index]) {
      if (std::abs(mark.offset - offset) <= kEpsilon) return mark.node;
    }
    const std::size_t node = graph_points.size();
    graph_points.push_back(point_at(input.edges[edge_index].path, offset));
    marks[edge_index].push_back({offset, node});
    return node;
  };
  const std::size_t origin_index =
      register_position(snap.edge_index, snap.offset_meters);

  struct EntranceBinding {
    std::size_t facility_index;
    std::string id;
    std::string access_edge_id;
    Point street_point;
    bool has_path;
    std::size_t node;
    double extra_seconds;
  };
  std::vector<EntranceBinding> bindings;
  for (std::size_t facility_index = 0;
       facility_index < input.facilities.size(); ++facility_index) {
    const FacilityAccess& facility = input.facilities[facility_index];
    const auto add_entrance = [&](const FacilityEntrance& entrance) {
      const std::size_t edge_index = edge_indices.at(entrance.access_edge_id);
      const Snap access = project_to_edge(
          entrance.street_access_point, input.edges[edge_index], edge_index);
      const std::size_t node = register_position(edge_index, access.offset_meters);
      const double connector_length = access.distance_meters +
          path_length(entrance.access_path);
      bindings.push_back({facility_index, entrance.id, entrance.access_edge_id,
          entrance.street_access_point, !entrance.access_path.empty(), node,
          connector_length / input.walking_speed_meters_per_second});
    };
    if (facility.entrances.empty()) {
      add_entrance({facility.id, facility.access_edge_id,
                    facility.access_point, {}});
    } else {
      for (const FacilityEntrance& entrance : facility.entrances) {
        add_entrance(entrance);
      }
    }
  }

  std::vector<InternalEdge> edges;
  for (std::size_t i = 0; i < input.edges.size(); ++i) {
    const WalkEdge& source = input.edges[i];
    auto& points = marks[i];
    std::sort(points.begin(), points.end(), [](Mark a, Mark b) {
      return a.offset < b.offset;
    });
    for (std::size_t j = 1; j < points.size(); ++j) {
      const Mark from = points[j - 1];
      const Mark to = points[j];
      if (to.offset - from.offset <= kEpsilon) continue;
      edges.push_back({source.id, source.kind, from.node, to.node,
          slice(source.path, from.offset, to.offset), to.offset - from.offset,
          from.offset, source.width_meters,
          source.kind == EdgeKind::crossing
              ? source.wait_seconds.value_or(input.crossing_wait_seconds)
              : 0.0});
    }
  }

  std::vector<std::vector<Arc>> adjacency(graph_points.size());
  bool blocked_crossing = false;
  for (const auto& edge : edges) {
    const double cost = edge.length / input.walking_speed_meters_per_second +
                        edge.wait_seconds;
    adjacency[edge.from].push_back({edge.to, cost});
    adjacency[edge.to].push_back({edge.from, cost});
  }
  const std::vector<double> arrival = shortest_times(adjacency, {{
      origin_index, access_seconds}});
  for (std::size_t i = 0; i < input.nodes.size(); ++i) {
    if (arrival[i] <= input.threshold_seconds) ++result.reachable_node_count;
  }
  for (std::size_t i = 0; i < graph_points.size(); ++i) {
    if (std::abs(arrival[i] - input.threshold_seconds) <= kEpsilon) {
      result.frontier.push_back(graph_points[i]);
    }
  }

  for (std::size_t i = 0; i < input.facilities.size(); ++i) {
    const FacilityAccess& facility = input.facilities[i];
    double best_time = std::numeric_limits<double>::infinity();
    std::string best_edge;
    std::optional<std::string> best_entrance;
    for (const EntranceBinding& binding : bindings) {
      if (binding.facility_index != i) continue;
      if (best_edge.empty()) best_edge = binding.access_edge_id;
      double candidate = arrival[binding.node] + binding.extra_seconds;
      if (!binding.has_path && binding.access_edge_id ==
              input.edges[snap.edge_index].id &&
          distance(binding.street_point, input.origin) <= kEpsilon) {
        candidate = 0.0;
      }
      if (candidate < best_time) {
        best_time = candidate;
        best_edge = binding.access_edge_id;
        best_entrance = binding.id;
      }
    }
    result.facility_travel_times.push_back({facility.id, best_edge,
        std::isfinite(best_time) ? std::optional<double>{best_time} : std::nullopt,
        best_time <= input.threshold_seconds + kEpsilon,
        facility.category, best_entrance});
  }

  double reachable_street_length = 0.0;
  std::vector<TimedDisplayEdge> timed_edges;
  for (const auto& edge : edges) {
    const auto intervals = reachable_intervals(
        edge, arrival, input.threshold_seconds,
        input.walking_speed_meters_per_second);
    if (edge.kind == EdgeKind::crossing) {
      if (!intervals.empty()) ++result.reachable_crossing_count;
      else if (arrival[edge.from] <= input.threshold_seconds + kEpsilon ||
               arrival[edge.to] <= input.threshold_seconds + kEpsilon) {
        blocked_crossing = true;
      }
    }
    for (const Interval part : intervals) {
      timed_edges.push_back({&edge, part});
      result.reachable_edges.push_back({edge.source_id, edge.kind,
          slice(edge.path, part.from, part.to), edge.width_meters});
      if (part.from > kEpsilon) {
        result.frontier.push_back(point_at(edge.path, part.from));
      }
      if (part.to < edge.length - kEpsilon) {
        result.frontier.push_back(point_at(edge.path, part.to));
      }
      if (edge.kind == EdgeKind::sidewalk || edge.kind == EdgeKind::shared_way) {
        reachable_street_length += part.to - part.from;
      }
    }
  }
  RoadClosureFillStats closure_stats;
  result.display_polygons = isochrone_polygons(
      result.reachable_edges, timed_edges, arrival, input.threshold_seconds,
      input.walking_speed_meters_per_second,
      input.display_area_radius_meters, input.display_buffer_meters,
      input.display_grid_step_meters,
      input.display_min_hole_area_square_meters, closure_stats);
  result.closed_road_face_count = closure_stats.face_count;
  result.road_closure_filled_cell_count = closure_stats.filled_cell_count;

  for (const ServiceCategory& category : input.service_categories) {
    GrayZone zone;
    zone.category = category.id;
    zone.status = category.data_status == "reviewed_online"
        ? "candidate" : "data_insufficient";
    zone.reachable_length_meters = reachable_street_length;
    if (category.data_status == "reviewed_online") {
      std::vector<std::pair<std::size_t, double>> sources;
      for (const EntranceBinding& binding : bindings) {
        if (input.facilities[binding.facility_index].category == category.id) {
          sources.emplace_back(binding.node, binding.extra_seconds);
        }
      }
      const std::vector<double> service_times = shortest_times(adjacency, sources);
      for (const InternalEdge& edge : edges) {
        if (edge.kind != EdgeKind::sidewalk &&
            edge.kind != EdgeKind::shared_way) continue;
        const auto from_origin = reachable_intervals(
            edge, arrival, input.threshold_seconds,
            input.walking_speed_meters_per_second);
        const auto from_facilities = reachable_intervals(
            edge, service_times, input.threshold_seconds,
            input.walking_speed_meters_per_second);
        for (const Interval part : subtract_intervals(
                 from_origin, from_facilities)) {
          zone.uncovered_edges.push_back({edge.source_id, edge.kind,
              slice(edge.path, part.from, part.to), edge.width_meters});
          zone.uncovered_length_meters += part.to - part.from;
        }
      }
      zone.display_polygons = display_polygons(
          zone.uncovered_edges, input.display_buffer_meters,
          input.display_grid_step_meters);
    }
    result.gray_zones.push_back(std::move(zone));
  }
  if (result.frontier.empty()) {
    result.warnings.push_back(blocked_crossing
        ? "CROSSING_NOT_COMPLETED_WITHIN_THRESHOLD"
        : "NETWORK_MAY_END_BEFORE_TIME_LIMIT");
  }
  if (result.display_polygons.empty()) {
    result.warnings.push_back("NO_DISPLAY_POLYGON");
  }
  return result;
}

}  // namespace isochrone
