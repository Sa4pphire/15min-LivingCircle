#include "isochrone/json_io.hpp"

#include <cctype>
#include <iomanip>
#include <sstream>

namespace isochrone {
namespace {

std::string escape_json(const std::string_view value) {
  std::string escaped;
  escaped.reserve(value.size());
  for (const char character : value) {
    switch (character) {
      case '"':
        escaped += "\\\"";
        break;
      case '\\':
        escaped += "\\\\";
        break;
      case '\n':
        escaped += "\\n";
        break;
      case '\r':
        escaped += "\\r";
        break;
      case '\t':
        escaped += "\\t";
        break;
      default:
        if (static_cast<unsigned char>(character) < 0x20) {
          constexpr char digits[] = "0123456789abcdef";
          escaped += "\\u00";
          escaped += digits[(static_cast<unsigned char>(character) >> 4) & 0xf];
          escaped += digits[static_cast<unsigned char>(character) & 0xf];
        } else {
          escaped += character;
        }
        break;
    }
  }
  return escaped;
}

}  // namespace

std::string read_all(std::istream& input) {
  std::ostringstream buffer;
  buffer << input.rdbuf();
  return buffer.str();
}

bool has_non_whitespace(const std::string_view input) {
  for (const unsigned char character : input) {
    if (!std::isspace(character)) {
      return true;
    }
  }
  return false;
}

std::string health_json() {
  return R"({"status":"ok","engine":"isochrone_engine","schemaVersion":2})";
}

std::string error_json(const std::string_view code,
                       const std::string_view message) {
  return "{\"schemaVersion\":2,\"success\":false,\"error\":{\"code\":\"" +
         escape_json(code) + "\",\"message\":\"" + escape_json(message) +
         "\"}}";
}

std::string serialize_engine_result(const EngineResult& result) {
  std::ostringstream output;
  output << std::setprecision(15);
  auto write_point = [&output](Point point) {
    output << '[' << point.x << ',' << point.y << ']';
  };
  auto write_path = [&write_point, &output](const std::vector<Point>& path) {
    output << '[';
    for (std::size_t i = 0; i < path.size(); ++i) {
      if (i) output << ',';
      write_point(path[i]);
    }
    output << ']';
  };
  output << "{\"schemaVersion\":2,\"success\":true,\"result\":{"
         << "\"snappedOriginMeters\":";
  write_point(result.snapped_origin);
  output << ",\"snapDistanceMeters\":" << result.snap_distance_meters
         << ",\"reachableEdges\":[";
  for (std::size_t i = 0; i < result.reachable_edges.size(); ++i) {
    if (i) output << ',';
    const auto& edge = result.reachable_edges[i];
    output << "{\"edgeId\":\"" << escape_json(edge.edge_id)
           << "\",\"kind\":\"" << edge_kind_name(edge.kind)
           << "\",\"pathMeters\":";
    write_path(edge.path);
    output << '}';
  }
  output << "],\"frontierMeters\":";
  write_path(result.frontier);
  output << ",\"displayPolygonMeters\":[";
  for (std::size_t i = 0; i < result.display_polygons.size(); ++i) {
    if (i) output << ',';
    write_path(result.display_polygons[i]);
  }
  output << "],\"diagnostics\":{\"reachableNodeCount\":"
         << result.reachable_node_count
         << ",\"reachableCrossingCount\":"
         << result.reachable_crossing_count << ",\"warnings\":[";
  for (std::size_t i = 0; i < result.warnings.size(); ++i) {
    if (i) output << ',';
    output << '"' << escape_json(result.warnings[i]) << '"';
  }
  output << "]}}}";
  return output.str();
}

}  // namespace isochrone
