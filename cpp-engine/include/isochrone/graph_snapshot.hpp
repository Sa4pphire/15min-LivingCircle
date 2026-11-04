#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <unordered_map>

#include "isochrone/road_graph.hpp"

namespace isochrone {

// Value metadata only. File/JSON/digest handling belongs to the loader adapter.
struct GraphSnapshotMetadata {
  int snapshot_format_version{1};
  int normalization_version{1};
  std::string region_id;
  std::string geographic_coordinate_type;
  double origin_longitude{};
  double origin_latitude{};
  std::optional<std::string> source_package_version_id;
};

// Keys are owned strings; indices refer to the snapshot's owned graph arrays.
// No entries borrow the loader's JSON text or point into movable containers.
struct GraphIndex {
  std::unordered_map<std::string, std::size_t> node_indices;
  std::unordered_map<std::string, std::size_t> edge_indices;
};

struct GraphSnapshotStats {
  std::size_t source_bytes{};
  std::size_t node_count{};
  std::size_t edge_count{};
  std::size_t path_point_count{};
};

class GraphSnapshotLoader;

// The loader verifies artifact identity before constructing this object. The
// private constructor validates the graph and builds its reusable ID indices.
// Once published, the snapshot has no mutable accessors or request-local state.
// Its owner must outlive every synchronous computation borrowing it.
class GraphSnapshot final {
 public:
  ~GraphSnapshot() = default;
  GraphSnapshot(const GraphSnapshot&) = delete;
  GraphSnapshot& operator=(const GraphSnapshot&) = delete;
  GraphSnapshot(GraphSnapshot&&) = delete;
  GraphSnapshot& operator=(GraphSnapshot&&) = delete;

  [[nodiscard]] const RoadGraph& graph() const noexcept { return graph_; }
  [[nodiscard]] const GraphIndex& index() const noexcept { return index_; }
  [[nodiscard]] const GraphSnapshotMetadata& metadata() const noexcept {
    return metadata_;
  }
  [[nodiscard]] const GraphSnapshotStats& stats() const noexcept { return stats_; }
  [[nodiscard]] const std::string& id() const noexcept { return graph_id_; }

 private:
  friend class GraphSnapshotLoader;

  GraphSnapshot(RoadGraph graph, GraphSnapshotMetadata metadata,
                std::string graph_id, std::size_t source_bytes);

  RoadGraph graph_;
  GraphSnapshotMetadata metadata_;
  std::string graph_id_;
  GraphIndex index_;
  GraphSnapshotStats stats_;
};

}  // namespace isochrone
