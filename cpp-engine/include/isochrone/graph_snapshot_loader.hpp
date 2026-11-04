#pragma once

#include <filesystem>
#include <memory>
#include <string_view>

#include "isochrone/graph_snapshot.hpp"

namespace isochrone {

// Link the optional isochrone_snapshot_io target to use this adapter. File I/O
// and OpenSSL stay outside the computation library and the default CLI.
class GraphSnapshotLoader final {
 public:
  // expected_graph_id is "sha256:" followed by 64 lower-case hex digits for
  // the exact artifact bytes. A mismatch never selects or loads another file.
  // The returned snapshot owns all data; no view of the source buffer escapes.
  [[nodiscard]] static std::unique_ptr<const GraphSnapshot> load(
      const std::filesystem::path& artifact_path,
      std::string_view expected_graph_id);
};

}  // namespace isochrone
