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
    return candidate.surface_distance_meters <=
        (edge.kind == EdgeKind::shared_way ? 3.0 : 30.0);
  };
  auto nearest = candidates.end();
  for (auto candidate = candidates.begin(); candidate != candidates.end();
       ++candidate) {
    if (in_range(*candidate) &&
        (nearest == candidates.end() || candidate->surface_distance_meters <
                                           nearest->surface_distance_meters)) {
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
        (!share_endpoint(input.edges[selected->edge_index],
                         input.edges[nearest->edge_index]) &&
         selected->surface_distance_meters >
             nearest->surface_distance_meters + kOriginSideAmbiguityMeters)) {
      throw OriginNotOnWalkway(
          "originEdgeId is not on the nearest sidewalk side");
    }
    return *selected;
  }
  for (const Snap& candidate : candidates) {
    if (candidate.edge_index != nearest->edge_index &&
        !share_endpoint(input.edges[candidate.edge_index],
                        input.edges[nearest->edge_index]) &&
        in_range(candidate) && candidate.surface_distance_meters <=
            nearest->surface_distance_meters + kOriginSideAmbiguityMeters) {
      throw AmbiguousOriginSide(
          "multiple sidewalk sides are equally near; specify originEdgeId");
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

std::vector<DisplayPolygon> display_polygons(
    const std::vector<ReachableEdge>& reachable, double buffer,
    double grid_step) {
  if (reachable.empty()) return {};
  Bounds bounds{std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity(),
                -std::numeric_limits<double>::infinity()};
  double max_buffer = buffer;
  for (const auto& edge : reachable) {
    max_buffer = std::max(max_buffer, edge.width_meters / 2.0);
    for (const Point point : edge.path) {
      bounds.min_x = std::min(bounds.min_x, point.x);
      bounds.max_x = std::max(bounds.max_x, point.x);
      bounds.min_y = std::min(bounds.min_y, point.y);
      bounds.max_y = std::max(bounds.max_y, point.y);
    }
  }
  const double padding = max_buffer + grid_step * 3.0;
  bounds.min_x -= padding;
  bounds.max_x += padding;
  bounds.min_y -= padding;
  bounds.max_y += padding;
  if ((bounds.max_x - bounds.min_x) / grid_step > 1000.0 ||
      (bounds.max_y - bounds.min_y) / grid_step > 1000.0) {
    throw std::invalid_argument("display grid exceeds 1000 by 1000 cells");
  }
  Grid grid = create_grid(bounds, grid_step);
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
  std::vector<Ring> rings =
      build_rings(extract_contour_segments(grid, buffer), grid_step * 0.01);
  rings.erase(std::remove_if(rings.begin(), rings.end(), [](const Ring& ring) {
                return std::abs(signed_area(ring)) < 1.0;
              }),
              rings.end());
  // Marching Squares returns independent closed rings. Assign each ring to
  // the smallest enclosing ring, then use even/odd nesting depth to preserve
  // holes and islands instead of filling every ring as an exterior polygon.
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
  std::unordered_set<std::string> facility_ids;
  for (const auto& facility : input.facilities) {
    if (facility.id.empty() || !facility_ids.insert(facility.id).second ||
        !finite(facility.access_point)) {
      throw std::invalid_argument("invalid or duplicate facility access");
    }
    const auto edge = std::find_if(input.edges.begin(), input.edges.end(),
                                   [&](const WalkEdge& candidate) {
                                     return candidate.id == facility.access_edge_id;
                                   });
    if (edge == input.edges.end() ||
        (edge->kind != EdgeKind::sidewalk && edge->kind != EdgeKind::shared_way)) {
      throw std::invalid_argument("facility accessEdgeId must identify a traversable edge");
    }
    const Snap access = project_to_edge(facility.access_point, *edge, 0);
    if (access.surface_distance_meters > kFacilityAccessTolerance) {
      throw std::invalid_argument("facility access point is outside its walkable edge");
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
                       std::move(left), snap.offset_meters, 0.0,
                       edge.width_meters});
      edges.push_back({edge.id, edge.kind, origin_index, to,
                       std::move(right), selected_length - snap.offset_meters,
                       snap.offset_meters, edge.width_meters});
    } else {
      edges.push_back({edge.id, edge.kind, from, to, edge.path,
                       path_length(edge.path), 0.0, edge.width_meters});
    }
  }

  struct Arc { std::size_t to; double cost; };
  std::vector<std::vector<Arc>> adjacency(input.nodes.size() + 1);
  bool blocked_crossing = false;
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
    for (const Arc arc : adjacency[node]) {
      if (current_time + arc.cost < arrival[arc.to]) {
        arrival[arc.to] = current_time + arc.cost;
        queue.emplace(arrival[arc.to], arc.to);
      }
    }
  }
  for (std::size_t i = 0; i < input.nodes.size(); ++i) {
    if (arrival[i] <= input.threshold_seconds) ++result.reachable_node_count;
    if (std::abs(arrival[i] - input.threshold_seconds) <= kEpsilon) {
      result.frontier.push_back(input.nodes[i].point);
    }
  }
  if (origin_index == input.nodes.size() &&
      std::abs(arrival[origin_index] - input.threshold_seconds) <= kEpsilon) {
    result.frontier.push_back(snap.point);
  }

  for (const auto& facility : input.facilities) {
    const auto source = std::find_if(input.edges.begin(), input.edges.end(),
        [&](const WalkEdge& edge) { return edge.id == facility.access_edge_id; });
    const Snap access = project_to_edge(facility.access_point, *source, 0);
    double best_time = std::numeric_limits<double>::infinity();
    for (const auto& edge : edges) {
      if (edge.source_id != facility.access_edge_id ||
          access.offset_meters < edge.start_offset - kEpsilon ||
          access.offset_meters > edge.start_offset + edge.length + kEpsilon) {
        continue;
      }
      const double offset = std::clamp(access.offset_meters - edge.start_offset,
                                       0.0, edge.length);
      best_time = std::min(best_time,
          arrival[edge.from] + offset / input.walking_speed_meters_per_second);
      best_time = std::min(best_time,
          arrival[edge.to] + (edge.length - offset) /
                              input.walking_speed_meters_per_second);
    }
    if (std::isfinite(best_time)) {
      best_time += access.distance_meters /
                   input.walking_speed_meters_per_second;
      if (facility.access_edge_id == selected.id &&
          distance(facility.access_point, input.origin) <= kEpsilon) {
        best_time = 0.0;
      }
    }
    result.facility_travel_times.push_back({facility.id, facility.access_edge_id,
        std::isfinite(best_time) ? std::optional<double>{best_time} : std::nullopt,
        best_time <= input.threshold_seconds + kEpsilon});
  }

  for (const auto& edge : edges) {
    const double from_time = arrival[edge.from];
    const double to_time = arrival[edge.to];
    if (edge.kind == EdgeKind::crossing) {
      const double cost = edge.length / input.walking_speed_meters_per_second +
                          input.crossing_wait_seconds;
      if (from_time + cost <= input.threshold_seconds + kEpsilon ||
          to_time + cost <= input.threshold_seconds + kEpsilon) {
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind, edge.path, edge.width_meters});
        ++result.reachable_crossing_count;
      } else if (from_time <= input.threshold_seconds + kEpsilon ||
                 to_time <= input.threshold_seconds + kEpsilon) {
        blocked_crossing = true;
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
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind, edge.path, edge.width_meters});
      }
    } else {
      if (from_reach > kEpsilon) {
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind, slice(edge.path, 0.0, from_reach),
             edge.width_meters});
        result.frontier.push_back(point_at(edge.path, from_reach));
      }
      if (to_reach > kEpsilon) {
        result.reachable_edges.push_back(
            {edge.source_id, edge.kind,
             slice(edge.path, edge.length - to_reach, edge.length),
             edge.width_meters});
        result.frontier.push_back(point_at(edge.path, edge.length - to_reach));
      }
    }
  }
  result.display_polygons = display_polygons(
      result.reachable_edges, input.display_buffer_meters,
      input.display_grid_step_meters);
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
