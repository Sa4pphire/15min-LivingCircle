#pragma once

#include <optional>
#include <string>
#include <vector>

#include "isochrone/types.hpp"

namespace isochrone {

enum class EdgeKind { walkway, turn, crossing };
enum class WalkAccessMode { separated, shared };

struct WalkNode {
  std::string id;
  Point point;
};

struct WalkEdge {
  std::string id;
  std::string from;
  std::string to;
  EdgeKind kind{EdgeKind::walkway};
  std::vector<Point> path;
  std::string street_block_id;
  std::string side;
  std::string shared_way_type;
  double width_meters{};
  std::optional<double> wait_seconds;
  // One walking kind; retain the original lateral-access/side constraints.
  WalkAccessMode access_mode{WalkAccessMode::separated};
};

[[nodiscard]] inline bool is_shared_walkway(const WalkEdge& edge) {
  return edge.kind == EdgeKind::walkway && edge.access_mode == WalkAccessMode::shared;
}

// Owns the base graph. Calculations borrow it as const and keep temporary
// splits and search state outside this object. The caller must keep it alive
// and must not mutate it while a calculation is using it.
struct RoadGraph {
  std::vector<WalkNode> nodes;
  std::vector<WalkEdge> edges;
};

[[nodiscard]] const char* edge_kind_name(EdgeKind kind);

}  // namespace isochrone
