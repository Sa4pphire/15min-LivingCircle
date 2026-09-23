#include "isochrone/engine.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <queue>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "isochrone/grid.hpp"
#include "isochrone/marching_squares.hpp"
#include "isochrone/ring_builder.hpp"

namespace isochrone {
namespace {

constexpr double kEndpointTolerance = 1.0;
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
  double offset_meters{};
};

Snap snap_origin(const EngineInput& input) {
  Snap best;
  bool matching_edge = false;
  for (std::size_t edge_index = 0; edge_index < input.edges.size();
       ++edge_index) {
    const auto& edge = input.edges[edge_index];
    if (input.origin_edge_id && edge.id != *input.origin_edge_id) continue;
    matching_edge = true;
    if (edge.kind != EdgeKind::sidewalk) {
      throw std::invalid_argument("originEdgeId must identify a sidewalk");
    }
    double walked = 0.0;
    for (std::size_t i = 1; i < edge.path.size(); ++i) {
      const Point a = edge.path[i - 1];
      const Point b = edge.path[i];
      const double dx = b.x - a.x;
      const double dy = b.y - a.y;
      const double squared_length = dx * dx + dy * dy;
      const double fraction = squared_length > 0.0
                                  ? std::clamp(((input.origin.x - a.x) * dx +
                                                (input.origin.y - a.y) * dy) /
                                                   squared_length,
                                               0.0, 1.0)
                                  : 0.0;
      const Point projection = interpolate(a, b, fraction);
      const double candidate_distance = distance(input.origin, projection);
      if (candidate_distance < best.distance_meters) {
        best = {edge_index, projection, candidate_distance,
                walked + fraction * std::sqrt(squared_length)};
      }
      walked += std::sqrt(squared_length);
    }
  }
  if (input.origin_edge_id && !matching_edge) {
    throw std::invalid_argument("originEdgeId does not exist");
  }
  if (!std::isfinite(best.distance_meters) || best.distance_meters > 30.0) {
    throw OriginNotOnWalkway("origin is farther than 30 m from a sidewalk");
  }
  return best;
}

struct InternalEdge {
  std::string source_id;
  EdgeKind kind;
  std::size_t from{};
  std::size_t to{};
  std::vector<Point> path;
  double length{};
};

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

std::vector<Ring> display_polygons(
    const std::vector<ReachableEdge>& reachable, double buffer,
    double grid_step) {
  if (reachable.empty()) return {};
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
  const double padding = buffer + grid_step * 3.0;
  bounds.min_x -= padding;
  bounds.max_x += padding;
  bounds.min_y -= padding;
  bounds.max_y += padding;
  if ((bounds.max_x - bounds.min_x) / grid_step > 1000.0 ||
      (bounds.max_y - bounds.min_y) / grid_step > 1000.0) {
    throw std::invalid_argument("display grid exceeds 1000 by 1000 cells");
  }
  Grid grid = create_grid(bounds, grid_step);
  std::fill(grid.values.begin(), grid.values.end(), buffer + grid_step);
  for (const auto& edge : reachable) {
    for (std::size_t i = 1; i < edge.path.size(); ++i) {
      const Point a = edge.path[i - 1];
      const Point b = edge.path[i];
      const double reach = buffer + grid_step;
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
          const double candidate = segment_distance(grid.point_at(row, col), a, b);
          grid.at(row, col) = std::min(grid.at(row, col), candidate);
        }
      }
    }
  }
  std::vector<Ring> rings =
      build_rings(extract_contour_segments(grid, buffer), grid_step * 0.01);
  rings.erase(std::remove_if(rings.begin(), rings.end(), [](const Ring& ring) {
                return std::abs(signed_area(ring)) < 1.0;
              }),
              rings.end());
  return rings;
}

}  // namespace

const char* edge_kind_name(EdgeKind kind) {
  switch (kind) {
    case EdgeKind::sidewalk: return "sidewalk";
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
      !std::isfinite(input.display_buffer_meters) ||
      input.display_buffer_meters <= 0.0 ||
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
  for (const auto& edge : input.edges) {
    if (edge.id.empty() || !edge_ids.insert(edge.id).second ||
        edge.from == edge.to || !nodes.count(edge.from) ||
        !nodes.count(edge.to) || edge.path.size() < 2 ||
        edge.path.size() > 1000) {
      throw std::invalid_argument("invalid walking edge identity or endpoints");
    }
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
  }
}

EngineResult compute_reachability(const EngineInput& input) {
  validate_graph(input);
  const Snap snap = snap_origin(input);
  EngineResult result;
  result.snapped_origin = snap.point;
  result.snap_distance_meters = snap.distance_meters;
  if (snap.distance_meters > 10.0) {
    result.warnings.push_back("ORIGIN_SNAP_OVER_10_METERS");
  }

  std::unordered_map<std::string, std::size_t> node_indices;
  for (std::size_t i = 0; i < input.nodes.size(); ++i) {
    node_indices.emplace(input.nodes[i].id, i);
  }
  std::vector<InternalEdge> edges;
  std::size_t origin_index = input.nodes.size();
  const auto& selected = input.edges[snap.edge_index];
  const double selected_length = path_length(selected.path);
  if (snap.offset_meters <= kEpsilon) {
    origin_index = node_indices.at(selected.from);
  } else if (selected_length - snap.offset_meters <= kEpsilon) {
    origin_index = node_indices.at(selected.to);
  }
  for (std::size_t i = 0; i < input.edges.size(); ++i) {
    const auto& edge = input.edges[i];
    const std::size_t from = node_indices.at(edge.from);
    const std::size_t to = node_indices.at(edge.to);
    if (i == snap.edge_index && origin_index == input.nodes.size()) {
      auto left = slice(edge.path, 0.0, snap.offset_meters);
      auto right = slice(edge.path, snap.offset_meters, selected_length);
      edges.push_back({edge.id, edge.kind, from, origin_index,
                       std::move(left), snap.offset_meters});
      edges.push_back({edge.id, edge.kind, origin_index, to,
                       std::move(right), selected_length - snap.offset_meters});
    } else {
      edges.push_back({edge.id, edge.kind, from, to, edge.path,
                       path_length(edge.path)});
    }
  }

  struct Arc { std::size_t to; double cost; };
  std::vector<std::vector<Arc>> adjacency(input.nodes.size() + 1);
  for (const auto& edge : edges) {
    const double cost = edge.length / input.walking_speed_meters_per_second +
                        (edge.kind == EdgeKind::crossing
                             ? input.crossing_wait_seconds
                             : 0.0);
    adjacency[edge.from].push_back({edge.to, cost});
    adjacency[edge.to].push_back({edge.from, cost});
  }
  std::vector<double> arrival(adjacency.size(),
                              std::numeric_limits<double>::infinity());
  using QueueItem = std::pair<double, std::size_t>;
  std::priority_queue<QueueItem, std::vector<QueueItem>,
                      std::greater<QueueItem>> queue;
  arrival[origin_index] = snap.distance_meters /
                          input.walking_speed_meters_per_second;
  queue.emplace(arrival[origin_index], origin_index);
  while (!queue.empty()) {
    const auto [current_time, node] = queue.top();
    queue.pop();
    if (current_time > arrival[node] + kEpsilon) continue;
    if (current_time > input.threshold_seconds) continue;
    for (const Arc arc : adjacency[node]) {
      if (current_time + arc.cost < arrival[arc.to]) {
        arrival[arc.to] = current_time + arc.cost;
        queue.emplace(arrival[arc.to], arc.to);
      }
    }
  }
  for (std::size_t i = 0; i < input.nodes.size(); ++i) {
    if (arrival[i] <= input.threshold_seconds) ++result.reachable_node_count;
  }

  for (const auto& edge : edges) {
    const double from_time = arrival[edge.from];
    const double to_time = arrival[edge.to];
    if (edge.kind == EdgeKind::crossing) {
      const double cost = edge.length / input.walking_speed_meters_per_second +
                          input.crossing_wait_seconds;
      if (from_time + cost <= input.threshold_seconds + kEpsilon ||
          to_time + cost <= input.threshold_seconds + kEpsilon) {
        result.reachable_edges.push_back({edge.source_id, edge.kind, edge.path});
        ++result.reachable_crossing_count;
      }
      continue;
    }
    const double from_reach = std::isfinite(from_time)
        ? std::clamp((input.threshold_seconds - from_time) *
                         input.walking_speed_meters_per_second,
                     0.0, edge.length)
        : 0.0;
    const double to_reach = std::isfinite(to_time)
        ? std::clamp((input.threshold_seconds - to_time) *
                         input.walking_speed_meters_per_second,
                     0.0, edge.length)
        : 0.0;
    if (from_reach + to_reach >= edge.length - kEpsilon) {
      if (from_reach > kEpsilon || to_reach > kEpsilon) {
        result.reachable_edges.push_back({edge.source_id, edge.kind, edge.path});
      }
    } else {
      if (from_reach > kEpsilon) {
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind, slice(edge.path, 0.0, from_reach)});
        result.frontier.push_back(point_at(edge.path, from_reach));
      }
      if (to_reach > kEpsilon) {
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind,
             slice(edge.path, edge.length - to_reach, edge.length)});
        result.frontier.push_back(point_at(edge.path, edge.length - to_reach));
      }
    }
  }
  result.display_polygons = display_polygons(
      result.reachable_edges, input.display_buffer_meters,
      input.display_grid_step_meters);
  if (result.frontier.empty()) {
    result.warnings.push_back("NETWORK_MAY_END_BEFORE_TIME_LIMIT");
  }
  if (result.display_polygons.empty()) {
    result.warnings.push_back("NO_DISPLAY_POLYGON");
  }
  return result;
}

}  // namespace isochrone
