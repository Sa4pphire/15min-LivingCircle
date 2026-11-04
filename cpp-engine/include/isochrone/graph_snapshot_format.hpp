#pragma once

#include <cstddef>

#include "isochrone/graph_snapshot.hpp"

namespace isochrone {

// Applies to the entire JSON artifact, including coordinate/context metadata.
// This bounds input bytes, not DOM expansion or resident process memory.
inline constexpr std::size_t kMaxGraphSnapshotBytes = 20'000'000;

// Parsed data is not a validated snapshot. The loader checks its byte identity
// and the GraphSnapshot constructor validates the graph before publication.
// All returned strings/containers own their data; none borrow the JSON input.
struct ParsedGraphSnapshot {
  RoadGraph graph;
  GraphSnapshotMetadata metadata;
};

}  // namespace isochrone
