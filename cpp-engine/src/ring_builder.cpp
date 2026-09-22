#include "isochrone/ring_builder.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace isochrone {
namespace {

bool near(const Point& first, const Point& second, const double tolerance) {
  return std::hypot(first.x - second.x, first.y - second.y) <= tolerance;
}

}  // namespace

std::vector<Ring> build_rings(const std::vector<Segment>& segments,
                              const double endpoint_tolerance_meters) {
  if (!std::isfinite(endpoint_tolerance_meters) ||
      endpoint_tolerance_meters < 0.0) {
    throw std::invalid_argument(
        "endpoint tolerance must be finite and non-negative");
  }

  std::vector<Ring> rings;
  std::vector<bool> used(segments.size(), false);

  for (std::size_t start_index = 0; start_index < segments.size();
       ++start_index) {
    if (used[start_index]) {
      continue;
    }

    Ring ring{segments[start_index].first, segments[start_index].second};
    used[start_index] = true;

    while (true) {
      std::size_t best_index = segments.size();
      bool best_matches_first = false;
      double best_distance = std::numeric_limits<double>::infinity();

      for (std::size_t index = 0; index < segments.size(); ++index) {
        if (used[index]) {
          continue;
        }

        const double distance_to_first =
            std::hypot(ring.back().x - segments[index].first.x,
                       ring.back().y - segments[index].first.y);
        if (distance_to_first <= endpoint_tolerance_meters &&
            distance_to_first < best_distance) {
          best_index = index;
          best_matches_first = true;
          best_distance = distance_to_first;
        }

        const double distance_to_second =
            std::hypot(ring.back().x - segments[index].second.x,
                       ring.back().y - segments[index].second.y);
        if (distance_to_second <= endpoint_tolerance_meters &&
            distance_to_second < best_distance) {
          best_index = index;
          best_matches_first = false;
          best_distance = distance_to_second;
        }
      }

      const double closure_distance =
          std::hypot(ring.back().x - ring.front().x,
                     ring.back().y - ring.front().y);
      const bool can_close = ring.size() >= 4 &&
                             closure_distance <= endpoint_tolerance_meters;
      if (can_close && closure_distance <= best_distance) {
        break;
      }
      if (best_index == segments.size()) {
        break;
      }
      ring.push_back(best_matches_first ? segments[best_index].second
                                        : segments[best_index].first);
      used[best_index] = true;
    }

    if (ring.size() >= 4 &&
        near(ring.back(), ring.front(), endpoint_tolerance_meters)) {
      ring.back() = ring.front();
      rings.push_back(std::move(ring));
    }
  }
  return rings;
}

double signed_area(const Ring& ring) {
  if (ring.size() < 4) {
    return 0.0;
  }
  double twice_area = 0.0;
  for (std::size_t index = 0; index + 1 < ring.size(); ++index) {
    twice_area += ring[index].x * ring[index + 1].y -
                  ring[index + 1].x * ring[index].y;
  }
  return twice_area / 2.0;
}

bool contains_point(const Ring& ring, const Point& point) {
  if (ring.size() < 4) {
    return false;
  }
  bool inside = false;
  for (std::size_t current = 0, previous = ring.size() - 1;
       current < ring.size(); previous = current++) {
    const Point& a = ring[current];
    const Point& b = ring[previous];
    const bool crosses = ((a.y > point.y) != (b.y > point.y)) &&
                         (point.x < (b.x - a.x) * (point.y - a.y) /
                                            (b.y - a.y) +
                                        a.x);
    if (crosses) {
      inside = !inside;
    }
  }
  return inside;
}

std::optional<Ring> select_primary_ring(const std::vector<Ring>& rings,
                                        const Point& center) {
  const Ring* selected = nullptr;
  double selected_area = -1.0;

  for (const auto& ring : rings) {
    if (!contains_point(ring, center)) {
      continue;
    }
    const double area = std::abs(signed_area(ring));
    if (area > selected_area) {
      selected = &ring;
      selected_area = area;
    }
  }
  if (selected == nullptr) {
    for (const auto& ring : rings) {
      const double area = std::abs(signed_area(ring));
      if (area > selected_area) {
        selected = &ring;
        selected_area = area;
      }
    }
  }
  if (selected == nullptr) {
    return std::nullopt;
  }

  Ring result = *selected;
  if (signed_area(result) < 0.0) {
    std::reverse(result.begin(), result.end());
  }
  return result;
}

}  // namespace isochrone
