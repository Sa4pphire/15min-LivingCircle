#include "isochrone/graph_snapshot_loader.hpp"

#include <algorithm>
#include <array>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

#include <openssl/evp.h>

#include "isochrone/graph_identity.hpp"
#include "isochrone/graph_snapshot_format.hpp"
#include "isochrone/json_io.hpp"

namespace isochrone {
namespace {

constexpr std::string_view kDigestPrefix = "sha256:";

void validate_expected_id(std::string_view identity) {
  if (!is_graph_snapshot_id(identity)) {
    throw std::invalid_argument("snapshot graph_id must be sha256:<64 lowercase hex digits>");
  }
}

std::string read_artifact(const std::filesystem::path& path) {
  std::ifstream file(path, std::ios::binary);
  if (!file.is_open()) {
    throw std::runtime_error("cannot open graph snapshot artifact");
  }

  std::string bytes;
  std::array<char, 64 * 1024> buffer{};
  for (;;) {
    // Read at most one byte beyond the limit so an exactly-sized file is
    // accepted only after observing EOF. Never trust a prior file_size/stat.
    const std::size_t remaining = kMaxGraphSnapshotBytes - bytes.size();
    const std::size_t count = std::min(buffer.size(), remaining + 1);
    file.read(buffer.data(), static_cast<std::streamsize>(count));
    const std::size_t received = static_cast<std::size_t>(file.gcount());
    if (received > remaining) {
      throw std::invalid_argument("graph snapshot artifact is too large");
    }
    if (file.bad() || (file.fail() && !file.eof())) {
      throw std::runtime_error("cannot read graph snapshot artifact");
    }
    bytes.append(buffer.data(), received);
    if (file.eof()) break;
  }
  return bytes;
}

std::string graph_id_for(std::string_view bytes) {
  using DigestContext = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
  DigestContext context(EVP_MD_CTX_new(), &EVP_MD_CTX_free);
  if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1 ||
      EVP_DigestUpdate(context.get(), bytes.data(), bytes.size()) != 1) {
    throw std::runtime_error("cannot calculate graph snapshot digest");
  }

  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int length{};
  if (EVP_DigestFinal_ex(context.get(), digest.data(), &length) != 1 || length != 32) {
    throw std::runtime_error("cannot finalize graph snapshot digest");
  }

  constexpr char hex[] = "0123456789abcdef";
  std::string identity(kDigestPrefix);
  identity.reserve(kDigestPrefix.size() + 64);
  for (unsigned int index = 0; index < length; ++index) {
    identity.push_back(hex[digest[index] >> 4]);
    identity.push_back(hex[digest[index] & 0x0f]);
  }
  return identity;
}

}  // namespace

std::unique_ptr<const GraphSnapshot> GraphSnapshotLoader::load(
    const std::filesystem::path& artifact_path,
    std::string_view expected_graph_id) {
  validate_expected_id(expected_graph_id);
  const std::string bytes = read_artifact(artifact_path);
  const std::string actual_id = graph_id_for(bytes);
  if (actual_id != expected_graph_id) {
    throw std::invalid_argument("graph snapshot digest does not match expected graph_id");
  }

  // Parsing owns its strings/vectors and destroys the DOM before returning.
  // Construction validates graph invariants and builds the reusable indexes.
  // No partially constructed snapshot is visible if either step throws.
  ParsedGraphSnapshot parsed = parse_graph_snapshot(bytes);
  return std::unique_ptr<const GraphSnapshot>(new GraphSnapshot(
      std::move(parsed.graph), std::move(parsed.metadata), actual_id, bytes.size()));
}

}  // namespace isochrone
