#pragma once

#include <exception>
#include <memory>
#include <string_view>
#include <utility>

#include "isochrone/graph_identity.hpp"
#include "isochrone/graph_snapshot.hpp"

namespace isochrone::rpc {

enum class GraphSelectionFailure { InvalidId, NotLoaded, VersionMismatch };

class GraphSelectionError final : public std::exception {
 public:
  explicit GraphSelectionError(GraphSelectionFailure reason) noexcept : reason_(reason) {}

  [[nodiscard]] GraphSelectionFailure reason() const noexcept { return reason_; }

  [[nodiscard]] const char* what() const noexcept override {
    switch (reason_) {
      case GraphSelectionFailure::InvalidId:
        return "Invalid graph identity.";
      case GraphSelectionFailure::NotLoaded:
        return "No graph snapshot is loaded.";
      case GraphSelectionFailure::VersionMismatch:
        return "Requested graph does not match the loaded snapshot.";
    }
    return "Graph selection failed.";
  }

 private:
  GraphSelectionFailure reason_;
};

// One service owns one fixed snapshot (or none in legacy full-input mode).
// Readers only borrow it. There is no runtime replacement, eviction or lazy
// loading: the service/store must outlive all synchronous request handlers.
class FixedGraphStore final {
 public:
  explicit FixedGraphStore(std::unique_ptr<const GraphSnapshot> snapshot = {}) noexcept
      : snapshot_(std::move(snapshot)) {}

  FixedGraphStore(const FixedGraphStore&) = delete;
  FixedGraphStore& operator=(const FixedGraphStore&) = delete;
  FixedGraphStore(FixedGraphStore&&) = delete;
  FixedGraphStore& operator=(FixedGraphStore&&) = delete;

  [[nodiscard]] const GraphSnapshot& require(std::string_view identity) const {
    if (!is_graph_snapshot_id(identity)) {
      throw GraphSelectionError(GraphSelectionFailure::InvalidId);
    }
    if (!snapshot_) {
      throw GraphSelectionError(GraphSelectionFailure::NotLoaded);
    }
    if (identity != snapshot_->id()) {
      throw GraphSelectionError(GraphSelectionFailure::VersionMismatch);
    }
    return *snapshot_;
  }

  [[nodiscard]] const GraphSnapshot* snapshot() const noexcept {
    return snapshot_.get();
  }

 private:
  const std::unique_ptr<const GraphSnapshot> snapshot_;
};

}  // namespace isochrone::rpc
