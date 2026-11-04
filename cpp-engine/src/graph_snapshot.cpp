#include "isochrone/graph_snapshot.hpp"

#include <utility>

#include "detail/input_validation.hpp"

namespace isochrone {

GraphSnapshot::GraphSnapshot(RoadGraph graph, GraphSnapshotMetadata metadata,
                             std::string graph_id, std::size_t source_bytes)
    : graph_(std::move(graph)), metadata_(std::move(metadata)),
      graph_id_(std::move(graph_id)),
      index_(detail::validate_static_graph(graph_, ExecutionControl{})) {
  stats_.source_bytes = source_bytes;
  stats_.node_count = graph_.nodes.size();
  stats_.edge_count = graph_.edges.size();
  for (const auto& edge : graph_.edges) {
    stats_.path_point_count += edge.path.size();
  }
}

}  // namespace isochrone
