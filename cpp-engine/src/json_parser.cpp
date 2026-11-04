#include "isochrone/json_io.hpp"

#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace isochrone {
namespace {

struct JsonValue {
  enum class Type { null_value, boolean, number, string, array, object };
  Type type{Type::null_value};
  using Array = std::vector<JsonValue>;
  using Object = std::map<std::string, JsonValue, std::less<>>;
  // Numeric coordinates must not construct three unused STL containers each.
  // This particularly matters with MSVC Debug iterator bookkeeping.
  std::variant<std::monostate, bool, double, std::string, Array, Object> data;
};

class Parser {
 public:
  explicit Parser(std::string_view source) : source_(source) {}

  JsonValue parse() {
    JsonValue result = value(0);
    spaces();
    if (position_ != source_.size()) {
      fail("unexpected trailing JSON text");
    }
    return result;
  }

 private:
  [[noreturn]] void fail(const char* message) const {
    throw std::invalid_argument(std::string(message) + " at byte " +
                                std::to_string(position_));
  }

  void spaces() {
    while (position_ < source_.size() &&
           (source_[position_] == ' ' || source_[position_] == '\n' ||
            source_[position_] == '\r' || source_[position_] == '\t')) {
      ++position_;
    }
  }

  bool take(char expected) {
    spaces();
    if (position_ < source_.size() && source_[position_] == expected) {
      ++position_;
      return true;
    }
    return false;
  }

  void expect(char expected) {
    if (!take(expected)) {
      fail("unexpected JSON token");
    }
  }

  void literal(std::string_view expected) {
    if (source_.substr(position_, expected.size()) != expected) {
      fail("invalid JSON literal");
    }
    position_ += expected.size();
  }

  static void utf8(std::string& output, unsigned value) {
    if (value <= 0x7f) {
      output.push_back(static_cast<char>(value));
    } else if (value <= 0x7ff) {
      output.push_back(static_cast<char>(0xc0 | (value >> 6)));
      output.push_back(static_cast<char>(0x80 | (value & 0x3f)));
    } else {
      output.push_back(static_cast<char>(0xe0 | (value >> 12)));
      output.push_back(static_cast<char>(0x80 | ((value >> 6) & 0x3f)));
      output.push_back(static_cast<char>(0x80 | (value & 0x3f)));
    }
  }

  std::string quoted() {
    expect('"');
    const std::size_t start = position_;
    const char* cursor = source_.data() + position_;
    const char* end = source_.data() + source_.size();
    while (cursor != end && *cursor != '"' && *cursor != '\\') {
      if (static_cast<unsigned char>(*cursor) < 0x20) {
        position_ = static_cast<std::size_t>(cursor - source_.data());
        fail("unescaped control character");
      }
      ++cursor;
    }
    position_ = static_cast<std::size_t>(cursor - source_.data());
    if (cursor != end && *cursor == '"') {
      ++position_;
      return std::string(source_.substr(start, position_ - start - 1));
    }
    std::string output(source_.substr(start, position_ - start));
    while (position_ < source_.size()) {
      const unsigned char character =
          static_cast<unsigned char>(source_[position_++]);
      if (character == '"') {
        return output;
      }
      if (character < 0x20) {
        fail("unescaped control character");
      }
      if (character != '\\') {
        output.push_back(static_cast<char>(character));
        continue;
      }
      if (position_ == source_.size()) {
        fail("incomplete JSON escape");
      }
      const char escape = source_[position_++];
      switch (escape) {
        case '"': output.push_back('"'); break;
        case '\\': output.push_back('\\'); break;
        case '/': output.push_back('/'); break;
        case 'b': output.push_back('\b'); break;
        case 'f': output.push_back('\f'); break;
        case 'n': output.push_back('\n'); break;
        case 'r': output.push_back('\r'); break;
        case 't': output.push_back('\t'); break;
        case 'u': {
          if (position_ + 4 > source_.size()) {
            fail("incomplete unicode escape");
          }
          unsigned code = 0;
          for (int index = 0; index < 4; ++index) {
            const char digit = source_[position_++];
            code <<= 4;
            if (digit >= '0' && digit <= '9') code += digit - '0';
            else if (digit >= 'a' && digit <= 'f') code += digit - 'a' + 10;
            else if (digit >= 'A' && digit <= 'F') code += digit - 'A' + 10;
            else fail("invalid unicode escape");
          }
          if (code >= 0xd800 && code <= 0xdfff) {
            fail("unicode surrogate escapes are unsupported");
          }
          utf8(output, code);
          break;
        }
        default: fail("invalid JSON escape");
      }
    }
    fail("unterminated JSON string");
  }

  JsonValue value(unsigned depth) {
    if (depth > 64) fail("JSON nesting is too deep");
    spaces();
    if (position_ == source_.size()) fail("unexpected end of JSON");
    JsonValue result;
    const char first = source_[position_];
    if (first == '"') {
      result.type = JsonValue::Type::string;
      result.data = quoted();
    } else if (first == '{') {
      result.type = JsonValue::Type::object;
      result.data = JsonValue::Object{};
      auto& object = std::get<JsonValue::Object>(result.data);
      ++position_;
      if (!take('}')) {
        do {
          spaces();
          if (position_ == source_.size() || source_[position_] != '"') {
            fail("JSON object key must be a string");
          }
          const std::string key = quoted();
          expect(':');
          if (!object.emplace(key, value(depth + 1)).second) {
            fail("duplicate JSON object key");
          }
          if (take('}')) break;
          expect(',');
        } while (true);
      }
    } else if (first == '[') {
      result.type = JsonValue::Type::array;
      result.data = JsonValue::Array{};
      auto& values = std::get<JsonValue::Array>(result.data);
      values.reserve(2);
      ++position_;
      if (!take(']')) {
        do {
          values.push_back(value(depth + 1));
          if (take(']')) break;
          expect(',');
        } while (true);
      }
    } else if (first == 't') {
      literal("true");
      result.type = JsonValue::Type::boolean;
      result.data = true;
    } else if (first == 'f') {
      literal("false");
      result.type = JsonValue::Type::boolean;
      result.data = false;
    } else if (first == 'n') {
      literal("null");
    } else if (first == '-' || std::isdigit(static_cast<unsigned char>(first))) {
      const std::size_t start = position_;
      if (source_[position_] == '-') ++position_;
      if (position_ == source_.size()) fail("invalid JSON number");
      if (source_[position_] == '0') ++position_;
      else {
        if (!std::isdigit(static_cast<unsigned char>(source_[position_]))) {
          fail("invalid JSON number");
        }
        while (position_ < source_.size() &&
               std::isdigit(static_cast<unsigned char>(source_[position_]))) {
          ++position_;
        }
      }
      if (position_ < source_.size() && source_[position_] == '.') {
        ++position_;
        if (position_ == source_.size() ||
            !std::isdigit(static_cast<unsigned char>(source_[position_]))) {
          fail("invalid JSON fraction");
        }
        while (position_ < source_.size() &&
               std::isdigit(static_cast<unsigned char>(source_[position_]))) ++position_;
      }
      if (position_ < source_.size() &&
          (source_[position_] == 'e' || source_[position_] == 'E')) {
        ++position_;
        if (position_ < source_.size() &&
            (source_[position_] == '+' || source_[position_] == '-')) ++position_;
        if (position_ == source_.size() ||
            !std::isdigit(static_cast<unsigned char>(source_[position_]))) {
          fail("invalid JSON exponent");
        }
        while (position_ < source_.size() &&
               std::isdigit(static_cast<unsigned char>(source_[position_]))) ++position_;
      }
      result.type = JsonValue::Type::number;
      double parsed{};
#if defined(_MSC_VER) || (defined(__GNUC__) && __GNUC__ >= 11)
      const auto converted = std::from_chars(source_.data() + start,
          source_.data() + position_, parsed, std::chars_format::general);
      if (converted.ec != std::errc{} || converted.ptr != source_.data() + position_) {
        fail("invalid or out-of-range JSON number");
      }
#else
      // Older MinGW lacks floating-point from_chars.
      parsed = std::stod(std::string(source_.substr(start, position_ - start)));
#endif
      if (!std::isfinite(parsed)) fail("non-finite JSON number");
      result.data = parsed;
    } else {
      fail("invalid JSON value");
    }
    return result;
  }

  std::string_view source_;
  std::size_t position_{};
};

const JsonValue& field(const JsonValue& parent, const char* key) {
  if (parent.type != JsonValue::Type::object) {
    throw std::invalid_argument("expected JSON object");
  }
  const auto& object = std::get<JsonValue::Object>(parent.data);
  const auto found = object.find(key);
  if (found == object.end()) {
    throw std::invalid_argument(std::string("missing field: ") + key);
  }
  return found->second;
}

const JsonValue* optional_field(const JsonValue& parent, const char* key) {
  if (parent.type != JsonValue::Type::object) {
    throw std::invalid_argument("expected JSON object");
  }
  const auto& object = std::get<JsonValue::Object>(parent.data);
  const auto found = object.find(key);
  return found == object.end() ? nullptr : &found->second;
}

double number(const JsonValue& value) {
  if (value.type != JsonValue::Type::number) {
    throw std::invalid_argument("expected JSON number");
  }
  return std::get<double>(value.data);
}

std::string string(const JsonValue& value) {
  if (value.type != JsonValue::Type::string) {
    throw std::invalid_argument("expected JSON string");
  }
  return std::get<std::string>(value.data);
}

bool boolean(const JsonValue& value) {
  if (value.type != JsonValue::Type::boolean) {
    throw std::invalid_argument("expected JSON boolean");
  }
  return std::get<bool>(value.data);
}

const std::vector<JsonValue>& array(const JsonValue& value) {
  if (value.type != JsonValue::Type::array) {
    throw std::invalid_argument("expected JSON array");
  }
  return std::get<JsonValue::Array>(value.data);
}

Point point(const JsonValue& value) {
  return {number(field(value, "xMeters")), number(field(value, "yMeters"))};
}

Point path_point(const JsonValue& value) {
  const auto& pair = array(value);
  if (pair.size() != 2) {
    throw std::invalid_argument("path coordinate must contain x and y");
  }
  return {number(pair[0]), number(pair[1])};
}

EdgeKind edge_kind(const std::string& value) {
  if (value == "walkway" || value == "sidewalk" || value == "shared_way") return EdgeKind::walkway;
  if (value == "turn") return EdgeKind::turn;
  if (value == "crossing") return EdgeKind::crossing;
  throw std::invalid_argument("unknown edge kind: " + value);
}

JsonValue parse_root(std::string_view input) {
  if (input.size() > 20'000'000) {
    throw std::invalid_argument("engine input is too large");
  }
  JsonValue root = Parser(input).parse();
  const double version = number(field(root, "schemaVersion"));
  if (version != 2.0) {
    throw std::invalid_argument("schemaVersion must be 2");
  }
  return root;
}

void parse_local_experiment(const JsonValue& root, AnalysisRequest& result) {
  if (const auto* local = optional_field(root, "localExperiment")) {
    LocalExperiment experiment;
    if (const auto* status = optional_field(*local, "topologyStatus")) {
      experiment.topology_status = string(*status);
    }
    for (const auto& node_id : array(field(*local, "boundaryNodeIds"))) {
      experiment.boundary_node_ids.push_back(string(node_id));
    }
    result.local_experiment = std::move(experiment);
  }
}

RoadGraph parse_graph_fields(const JsonValue& root, bool canonical_only = false) {
  RoadGraph result;
  const auto& nodes = array(field(root, "nodes"));
  const auto& edges = array(field(root, "edges"));
  // The synthetic full-map fixture duplicates major-road centreline vertices
  // into separated sidewalk sides. Keep a bounded input, but allow that graph.
  if (nodes.size() > 30'000 || edges.size() > 50'000) {
    throw std::invalid_argument("walking graph exceeds engine limits");
  }
  result.nodes.reserve(nodes.size());
  result.edges.reserve(edges.size());
  for (const auto& value : nodes) {
    result.nodes.push_back({string(field(value, "id")), point(value)});
  }
  for (const auto& value : edges) {
    WalkEdge edge;
    edge.id = string(field(value, "id"));
    edge.from = string(field(value, "from"));
    edge.to = string(field(value, "to"));
    const std::string kind = string(field(value, "kind"));
    if (canonical_only && (kind == "sidewalk" || kind == "shared_way")) {
      throw std::invalid_argument("graph snapshot requires canonical walkway kind");
    }
    edge.kind = edge_kind(kind);
    if (edge.kind == EdgeKind::walkway) {
      const auto* declared = optional_field(value, "accessMode");
      const std::string mode = declared ? string(*declared)
          : kind == "sidewalk" ? "separated"
          : kind == "shared_way" ? "shared" : "";
      if ((mode != "separated" && mode != "shared") ||
          (kind == "sidewalk" && mode != "separated") ||
          (kind == "shared_way" && mode != "shared")) {
        throw std::invalid_argument("walkway requires consistent accessMode: separated or shared");
      }
      edge.access_mode = mode == "shared" ? WalkAccessMode::shared : WalkAccessMode::separated;
    } else if (optional_field(value, "accessMode")) {
      throw std::invalid_argument("accessMode is only valid on walkway");
    }
    if (const auto* block = optional_field(value, "streetBlockId")) {
      edge.street_block_id = string(*block);
    }
    if (const auto* side = optional_field(value, "side")) {
      edge.side = string(*side);
    }
    if (const auto* type = optional_field(value, "sharedWayType")) {
      edge.shared_way_type = string(*type);
    }
    if (const auto* width = optional_field(value, "widthMeters")) {
      edge.width_meters = number(*width);
    }
    if (const auto* wait = optional_field(value, "waitSeconds")) {
      edge.wait_seconds = number(*wait);
    }
    for (const auto& coordinate : array(field(value, "pathMeters"))) {
      edge.path.push_back(path_point(coordinate));
    }
    result.edges.push_back(std::move(edge));
  }
  return result;
}

void parse_request_collections(const JsonValue& root, AnalysisRequest& result) {
  if (const auto* facilities = optional_field(root, "facilities")) {
    if (array(*facilities).size() > 10'000) {
      throw std::invalid_argument("too many facilities");
    }
    for (const auto& value : array(*facilities)) {
      FacilityAccess facility;
      facility.id = string(field(value, "id"));
      if (const auto* category = optional_field(value, "category")) {
        facility.category = string(*category);
      }
      if (const auto* entrances = optional_field(value, "entrances")) {
        if (array(*entrances).size() > 100) {
          throw std::invalid_argument("too many entrances for one facility");
        }
        for (const auto& entry : array(*entrances)) {
          FacilityEntrance entrance;
          entrance.id = string(field(entry, "id"));
          entrance.access_edge_id = string(field(entry, "accessEdgeId"));
          entrance.street_access_point =
              path_point(field(entry, "streetAccessPointMeters"));
          if (const auto* path = optional_field(entry, "accessPathMeters")) {
            for (const auto& coordinate : array(*path)) {
              entrance.access_path.push_back(path_point(coordinate));
            }
          }
          facility.entrances.push_back(std::move(entrance));
        }
      } else {
        facility.access_edge_id = string(field(value, "accessEdgeId"));
        facility.access_point = path_point(field(value, "accessPointMeters"));
      }
      result.facilities.push_back(std::move(facility));
    }
  }
  if (const auto* categories = optional_field(root, "serviceCategories")) {
    if (array(*categories).size() > 10) {
      throw std::invalid_argument("too many service categories");
    }
    for (const auto& value : array(*categories)) {
      ServiceCategory category;
      category.id = string(field(value, "id"));
      category.data_status = string(field(value, "dataStatus"));
      if (const auto* inventory = optional_field(value, "localInventoryStatus")) {
        category.local_inventory_status = string(*inventory);
      }
      result.service_categories.push_back(std::move(category));
    }
  }
}

AnalysisRequest parse_request_parameters(const JsonValue& root) {
  AnalysisRequest result;
  result.origin = point(field(root, "originMeters"));
  if (const auto* value = optional_field(root, "originEdgeId")) {
    if (value->type != JsonValue::Type::null_value) {
      result.origin_edge_id = string(*value);
    }
  }
  parse_local_experiment(root, result);
  if (const auto* target = optional_field(root, "routeFacilityId")) {
    result.route_facility_id = string(*target);
  }
  if (const auto* route_only = optional_field(root, "routeOnly")) {
    result.route_only = boolean(*route_only);
  }
  if (const auto* facilities_only = optional_field(root, "facilitiesOnly")) {
    result.facilities_only = boolean(*facilities_only);
  }
  if (const auto* threshold = optional_field(root, "thresholdSeconds")) {
    result.threshold_seconds = number(*threshold);
  } else if (result.local_experiment) {
    result.threshold_seconds = 180.0;
  } else {
    result.threshold_seconds = number(field(root, "thresholdSeconds"));
  }
  result.walking_speed_meters_per_second =
      number(field(root, "walkingSpeedMetersPerSecond"));
  result.crossing_wait_seconds = number(field(root, "crossingWaitSeconds"));
  if (const auto* value = optional_field(root, "maxOriginSnapMeters")) {
    result.max_origin_snap_meters = number(*value);
  }
  if (const auto* value = optional_field(root, "allowOffNetworkOrigin")) {
    result.allow_off_network_origin = boolean(*value);
  }
  if (const auto* value = optional_field(root, "displayBufferMeters")) {
    result.display_buffer_meters = number(*value);
  }
  if (const auto* value = optional_field(root, "displayAreaRadiusMeters")) {
    result.display_area_radius_meters = number(*value);
  }
  if (const auto* value = optional_field(root, "displayMinHoleAreaSquareMeters")) {
    result.display_min_hole_area_square_meters = number(*value);
  }
  if (const auto* value = optional_field(root, "displayGridStepMeters")) {
    result.display_grid_step_meters = number(*value);
  }
  return result;
}

AnalysisRequest parse_request_fields(const JsonValue& root) {
  AnalysisRequest result = parse_request_parameters(root);
  parse_request_collections(root, result);
  return result;
}

// Keep legacy parse order: parameters -> graph -> facilities/categories ->
// joint validation. Request collections are deliberately not moved before graph.
EngineInput finish_complete_input(const JsonValue& root, EngineInput result) {
  result.graph = parse_graph_fields(root);
  parse_request_collections(root, result.request);
  validate_graph(result);
  return result;
}

void require_snapshot_version(const JsonValue& root, const char* key) {
  if (number(field(root, key)) != 1.0) {
    throw std::invalid_argument(std::string(key) + " must be 1");
  }
}

// Context geometry is validated for the Python preparation contract, but is
// not retained in the C++ calculation snapshot. The artifact's byte digest
// still covers it together with the graph and its coordinate frame.
void validate_context_ring(const JsonValue& value) {
  const auto& ring = array(value);
  if (ring.size() < 4) {
    throw std::invalid_argument("snapshot context ring must have at least four points");
  }
  const Point first = path_point(ring.front());
  Point last = first;
  for (const auto& coordinate : ring) {
    last = path_point(coordinate);
  }
  if (first.x != last.x || first.y != last.y) {
    throw std::invalid_argument("snapshot context ring must be closed");
  }
}

void validate_snapshot_context(const JsonValue& root) {
  const auto& bounds = field(root, "supportedCenterBoundsMeters");
  const double min_x = number(field(bounds, "minX"));
  const double max_x = number(field(bounds, "maxX"));
  const double min_y = number(field(bounds, "minY"));
  const double max_y = number(field(bounds, "maxY"));
  if (min_x > max_x || min_y > max_y) {
    throw std::invalid_argument("snapshot supported bounds must be ordered");
  }
  const bool synthetic = boolean(field(root, "synthetic"));
  const auto* areas = optional_field(root, "publicWalkableAreasMeters");
  if (!synthetic && (!areas || array(*areas).empty())) {
    throw std::invalid_argument("non-synthetic snapshot needs public walkable areas");
  }
  if (areas) {
    for (const auto& ring : array(*areas)) validate_context_ring(ring);
  }
  if (const auto* polygon = optional_field(root, "supportedCenterPolygonMeters")) {
    validate_context_ring(*polygon);
  }
}

GraphSnapshotMetadata parse_snapshot_metadata(const JsonValue& root) {
  require_snapshot_version(root, "snapshotFormatVersion");
  require_snapshot_version(root, "normalizationVersion");
  GraphSnapshotMetadata result;
  result.region_id = string(field(root, "regionId"));
  if (result.region_id.empty()) {
    throw std::invalid_argument("snapshot regionId must not be empty");
  }
  const auto& frame = field(root, "coordinateFrame");
  result.geographic_coordinate_type = string(field(frame, "geographicType"));
  if (result.geographic_coordinate_type != "bd09ll" &&
      result.geographic_coordinate_type != "wgs84ll") {
    throw std::invalid_argument("snapshot geographicType must be bd09ll or wgs84ll");
  }
  const auto& origin = field(frame, "origin");
  result.origin_longitude = number(field(origin, "lng"));
  result.origin_latitude = number(field(origin, "lat"));
  if (result.origin_longitude < -180.0 || result.origin_longitude > 180.0 ||
      result.origin_latitude < -90.0 || result.origin_latitude > 90.0) {
    throw std::invalid_argument("snapshot geographic origin is out of range");
  }
  if (const auto* source = optional_field(root, "sourcePackageVersionId")) {
    const std::string version = string(*source);
    if (version.size() != 64) {
      throw std::invalid_argument("sourcePackageVersionId must be lowercase SHA-256 hex");
    }
    for (char digit : version) {
      if (!((digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f'))) {
        throw std::invalid_argument("sourcePackageVersionId must be lowercase SHA-256 hex");
      }
    }
    result.source_package_version_id = version;
  }
  validate_snapshot_context(root);
  return result;
}

void reject_snapshot_request_fields(const JsonValue& root) {
  // An artifact supplies stable graph/context data, never request defaults.
  // Reject even empty fields so that a caller cannot mistake ignored data for
  // parameters that will actually be used by later calculations.
  for (const char* key : {
      "originMeters", "originEdgeId", "thresholdSeconds",
      "walkingSpeedMetersPerSecond", "crossingWaitSeconds",
      "maxOriginSnapMeters", "allowOffNetworkOrigin", "displayBufferMeters",
      "displayAreaRadiusMeters", "displayMinHoleAreaSquareMeters",
      "displayGridStepMeters", "facilities", "serviceCategories",
      "localExperiment", "routeFacilityId", "routeOnly", "facilitiesOnly"}) {
    if (optional_field(root, key)) {
      throw std::invalid_argument(std::string("graph snapshot must not contain request field: ") + key);
    }
  }
}

}  // namespace

EngineInput parse_engine_input(std::string_view input) {
  const JsonValue root = parse_root(input);
  EngineInput result;
  result.request = parse_request_parameters(root);
  return finish_complete_input(root, std::move(result));
}

AnalysisRequest parse_analysis_request(std::string_view input) {
  const JsonValue root = parse_root(input);
  if (optional_field(root, "nodes") || optional_field(root, "edges")) {
    throw std::invalid_argument("analysis request must not contain nodes or edges");
  }
  return parse_request_fields(root);
}

ParsedGraphSnapshot parse_graph_snapshot(std::string_view input) {
  if (input.size() > kMaxGraphSnapshotBytes) {
    throw std::invalid_argument("graph snapshot input is too large");
  }
  const JsonValue root = parse_root(input);
  reject_snapshot_request_fields(root);
  ParsedGraphSnapshot result;
  result.metadata = parse_snapshot_metadata(root);
  result.graph = parse_graph_fields(root, true);
  return result;
}

EngineInput parse_synthetic_network(std::string_view input, Point origin,
                                  std::optional<std::string> origin_edge_id,
                                  bool local_experiment) {
  const JsonValue root = parse_root(input);
  if (!boolean(field(root, "synthetic"))) {
    throw std::invalid_argument("file demo requires synthetic: true");
  }
  EngineInput result;
  result.request.origin = origin;
  result.request.origin_edge_id = std::move(origin_edge_id);
  result.request.display_min_hole_area_square_meters = 2500.0;
  result.request.allow_off_network_origin = !local_experiment;
  result.request.max_origin_snap_meters = local_experiment ? 5.0 : 1170.0;
  if (local_experiment) {
    result.request.threshold_seconds = 180.0;
    result.request.local_experiment = LocalExperiment{};
    // Missing boundary declarations stay incomplete; never infer them from degree.
    parse_local_experiment(root, result.request);
  } else if (optional_field(root, "localExperiment")) {
    throw std::invalid_argument("local experiment graph requires --local");
  }
  return finish_complete_input(root, std::move(result));
}

}  // namespace isochrone
