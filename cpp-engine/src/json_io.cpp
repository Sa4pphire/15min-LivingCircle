#include "isochrone/json_io.hpp"

#include <algorithm>
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
  auto write_polygons = [&write_path, &output](
                            const std::vector<DisplayPolygon>& polygons) {
    output << '[';
    for (std::size_t i = 0; i < polygons.size(); ++i) {
      if (i) output << ',';
      const auto& polygon = polygons[i];
      output << '[';
      write_path(polygon.outer);
      for (const auto& hole : polygon.holes) {
        output << ',';
        write_path(hole);
      }
      output << ']';
    }
    output << ']';
  };
  auto write_edge = [&write_path, &output](const ReachableEdge& edge) {
    output << "{\"edgeId\":\"" << escape_json(edge.edge_id)
           << "\",\"kind\":\"" << edge_kind_name(edge.kind)
           << "\",\"pathMeters\":";
    write_path(edge.path);
    if (edge.kind == EdgeKind::shared_way) {
      output << ",\"widthMeters\":" << edge.width_meters;
    }
    output << '}';
  };
  output << "{\"schemaVersion\":2,\"success\":true,\"result\":{"
         << "\"snappedOriginMeters\":";
  write_point(result.snapped_origin);
  output << ",\"snapDistanceMeters\":" << result.snap_distance_meters
         << ",\"originAccessSeconds\":" << result.origin_access_seconds
         << ",\"reachableEdges\":[";
  for (std::size_t i = 0; i < result.reachable_edges.size(); ++i) {
    if (i) output << ',';
    write_edge(result.reachable_edges[i]);
  }
  output << "],\"frontierMeters\":";
  write_path(result.frontier);
  output << ",\"displayPolygonMeters\":";
  write_polygons(result.display_polygons);
  output << ",\"displayGeometryMeters\":{\"type\":\"MultiPolygon\",\"coordinates\":";
  write_polygons(result.display_polygons);
  output << "},\"facilityTravelTimes\":[";
  for (std::size_t i = 0; i < result.facility_travel_times.size(); ++i) {
    if (i) output << ',';
    const auto& facility = result.facility_travel_times[i];
    output << "{\"id\":\"" << escape_json(facility.id)
           << "\",\"accessEdgeId\":\"" << escape_json(facility.access_edge_id)
           << "\",\"reachable\":" << (facility.reachable ? "true" : "false")
           << ",\"travelTimeSeconds\":";
    if (facility.travel_time_seconds) output << *facility.travel_time_seconds;
    else output << "null";
    output << ",\"category\":\"" << escape_json(facility.category)
           << "\",\"bestEntranceId\":";
    if (facility.best_entrance_id) {
      output << '"' << escape_json(*facility.best_entrance_id) << '"';
    } else {
      output << "null";
    }
    output << '}';
  }
  output << "],\"grayZones\":[";
  for (std::size_t i = 0; i < result.gray_zones.size(); ++i) {
    if (i) output << ',';
    const GrayZone& zone = result.gray_zones[i];
    output << "{\"category\":\"" << escape_json(zone.category)
           << "\",\"status\":\"" << escape_json(zone.status)
           << "\",\"uncoveredEdges\":[";
    for (std::size_t j = 0; j < zone.uncovered_edges.size(); ++j) {
      if (j) output << ',';
      write_edge(zone.uncovered_edges[j]);
    }
    output << "],\"displayGeometryMeters\":{\"type\":\"MultiPolygon\",\"coordinates\":";
    write_polygons(zone.display_polygons);
    output << "},\"reachableLengthMeters\":" << zone.reachable_length_meters
           << ",\"uncoveredLengthMeters\":";
    if (zone.status == "candidate") {
      output << zone.uncovered_length_meters;
    } else {
      output << "null";
    }
    output << ",\"uncoveredLengthRatio\":";
    if (zone.status == "candidate" && zone.reachable_length_meters > 0.0) {
      output << std::clamp(
          zone.uncovered_length_meters / zone.reachable_length_meters, 0.0, 1.0);
    } else if (zone.status == "candidate") {
      output << 0;
    } else {
      output << "null";
    }
    output << '}';
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
