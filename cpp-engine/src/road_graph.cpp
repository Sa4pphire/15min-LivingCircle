#include "isochrone/road_graph.hpp"

namespace isochrone {

const char* edge_kind_name(EdgeKind kind) {
  switch (kind) {
    case EdgeKind::walkway: return "walkway";
    case EdgeKind::turn: return "turn";
    case EdgeKind::crossing: return "crossing";
  }
  return "unknown";
}

}  // namespace isochrone
