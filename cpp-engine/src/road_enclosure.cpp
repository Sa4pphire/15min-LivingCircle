#include "isochrone/road_enclosure.hpp"

#include <algorithm>
#include <cmath>
#include <deque>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <unordered_set>

#include "isochrone/ring_builder.hpp"

namespace isochrone {
namespace {

struct HalfEdge {
  std::size_t from;
  std::size_t to;
  std::size_t link_index;
  bool reversed;
  double angle;
};

double departure_angle(const std::vector<Point>& path, bool reversed) {
  const Point start = reversed ? path.back() : path.front();
  for (std::size_t i = 1; i < path.size(); ++i) {
    const Point next = reversed ? path[path.size() - 1 - i] : path[i];
    if (std::hypot(next.x - start.x, next.y - start.y) > 1e-8) {
      return std::atan2(next.y - start.y, next.x - start.x);
    }
  }
  return std::numeric_limits<double>::quiet_NaN();
}

void append_path(Ring& ring, const RoadLink& link, bool reversed) {
  if (reversed) {
    for (auto point = link.path.rbegin(); point != link.path.rend(); ++point) {
      if (ring.empty() || point != link.path.rbegin()) ring.push_back(*point);
    }
  } else {
    for (auto point = link.path.begin(); point != link.path.end(); ++point) {
      if (ring.empty() || point != link.path.begin()) ring.push_back(*point);
    }
  }
}

}  // namespace

std::vector<RoadFace> closed_road_faces(
    const std::vector<RoadLink>& links, std::size_t node_count) {
  std::vector<std::vector<std::size_t>> incident(node_count);
  std::vector<std::size_t> degree(node_count, 0);
  std::vector<bool> active(links.size(), true);
  for (std::size_t i = 0; i < links.size(); ++i) {
    const RoadLink& link = links[i];
    if (link.from >= node_count || link.to >= node_count ||
        link.from == link.to || link.path.size() < 2) {
      throw std::invalid_argument("invalid road enclosure link");
    }
    incident[link.from].push_back(i);
    incident[link.to].push_back(i);
    ++degree[link.from];
    ++degree[link.to];
  }

  // A dangling branch cannot bound a street block. Prune it before walking
  // faces, so a branch inside a cycle cannot turn that cycle into a non-ring.
  std::deque<std::size_t> leaves;
  for (std::size_t node = 0; node < node_count; ++node) {
    if (degree[node] == 1) leaves.push_back(node);
  }
  while (!leaves.empty()) {
    const std::size_t node = leaves.front();
    leaves.pop_front();
    for (const std::size_t link_index : incident[node]) {
      if (!active[link_index]) continue;
      active[link_index] = false;
      const RoadLink& link = links[link_index];
      const std::size_t other = link.from == node ? link.to : link.from;
      --degree[node];
      if (--degree[other] == 1) leaves.push_back(other);
    }
  }

  std::vector<HalfEdge> halves;
  std::vector<std::vector<std::size_t>> outgoing(node_count);
  for (std::size_t i = 0; i < links.size(); ++i) {
    if (!active[i]) continue;
    const RoadLink& link = links[i];
    const double forward = departure_angle(link.path, false);
    const double backward = departure_angle(link.path, true);
    if (!std::isfinite(forward) || !std::isfinite(backward)) continue;
    const std::size_t first = halves.size();
    halves.push_back({link.from, link.to, i, false, forward});
    halves.push_back({link.to, link.from, i, true, backward});
    outgoing[link.from].push_back(first);
    outgoing[link.to].push_back(first + 1);
  }
  std::vector<std::size_t> positions(halves.size());
  for (auto& arcs : outgoing) {
    std::sort(arcs.begin(), arcs.end(), [&](std::size_t a, std::size_t b) {
      if (halves[a].angle != halves[b].angle) {
        return halves[a].angle < halves[b].angle;
      }
      return a < b;
    });
    for (std::size_t i = 0; i < arcs.size(); ++i) positions[arcs[i]] = i;
  }

  std::vector<RoadFace> faces;
  std::vector<bool> visited(halves.size(), false);
  for (std::size_t start = 0; start < halves.size(); ++start) {
    if (visited[start]) continue;
    std::vector<std::size_t> boundary;
    std::size_t current = start;
    while (!visited[current]) {
      visited[current] = true;
      boundary.push_back(current);
      const auto& arcs = outgoing[halves[current].to];
      const std::size_t twin_position = positions[current ^ 1U];
      current = arcs[(twin_position + arcs.size() - 1) % arcs.size()];
    }
    if (current != start || boundary.size() < 3) continue;

    RoadFace face;
    std::unordered_set<std::size_t> seen_nodes;
    bool simple = true;
    for (const std::size_t arc_index : boundary) {
      const HalfEdge& half = halves[arc_index];
      if (!seen_nodes.insert(half.from).second) {
        simple = false;
        break;
      }
      face.link_indices.push_back(half.link_index);
      append_path(face.ring, links[half.link_index], half.reversed);
    }
    if (!simple || face.ring.size() < 4 ||
        std::hypot(face.ring.front().x - face.ring.back().x,
                   face.ring.front().y - face.ring.back().y) > 1e-4) {
      continue;
    }
    face.ring.back() = face.ring.front();
    // With the face kept on the left, bounded faces wind counterclockwise;
    // the unbounded exterior winds clockwise and is not a street block.
    if (signed_area(face.ring) > 1.0) faces.push_back(std::move(face));
  }
  return faces;
}

}  // namespace isochrone
