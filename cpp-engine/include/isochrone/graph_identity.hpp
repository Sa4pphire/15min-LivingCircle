#pragma once

#include <string_view>

namespace isochrone {

// Shared by the file loader and RPC boundaries. This checks identity syntax;
// only the loader checks that an artifact's bytes actually have this digest.
[[nodiscard]] inline bool is_graph_snapshot_id(std::string_view identity) noexcept {
  constexpr std::string_view prefix = "sha256:";
  if (identity.size() != prefix.size() + 64 || !identity.starts_with(prefix)) {
    return false;
  }
  for (const char digit : identity.substr(prefix.size())) {
    if (!((digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f'))) {
      return false;
    }
  }
  return true;
}

}  // namespace isochrone
