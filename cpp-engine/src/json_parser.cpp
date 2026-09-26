#include "isochrone/json_io.hpp"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace isochrone {
namespace {

struct JsonValue {
  enum class Type { null_value, boolean, number, string, array, object };
  Type type{Type::null_value};
  bool boolean{};
  double number{};
  std::string string;
  std::vector<JsonValue> array;
  std::map<std::string, JsonValue> object;
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
    std::string output;
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
      result.string = quoted();
    } else if (first == '{') {
      result.type = JsonValue::Type::object;
      ++position_;
      if (!take('}')) {
        do {
          spaces();
          if (position_ == source_.size() || source_[position_] != '"') {
            fail("JSON object key must be a string");
          }
          const std::string key = quoted();
          expect(':');
          if (!result.object.emplace(key, value(depth + 1)).second) {
            fail("duplicate JSON object key");
          }
          if (take('}')) break;
          expect(',');
        } while (true);
      }
    } else if (first == '[') {
      result.type = JsonValue::Type::array;
      ++position_;
      if (!take(']')) {
        do {
          result.array.push_back(value(depth + 1));
          if (take(']')) break;
          expect(',');
        } while (true);
      }
    } else if (first == 't') {
      literal("true");
      result.type = JsonValue::Type::boolean;
      result.boolean = true;
    } else if (first == 'f') {
      literal("false");
      result.type = JsonValue::Type::boolean;
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
      result.number = std::stod(std::string(source_.substr(start, position_ - start)));
      if (!std::isfinite(result.number)) fail("non-finite JSON number");
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
  const auto found = parent.object.find(key);
  if (found == parent.object.end()) {
    throw std::invalid_argument(std::string("missing field: ") + key);
  }
  return found->second;
}

const JsonValue* optional_field(const JsonValue& parent, const char* key) {
  if (parent.type != JsonValue::Type::object) {
    throw std::invalid_argument("expected JSON object");
  }
  const auto found = parent.object.find(key);
  return found == parent.object.end() ? nullptr : &found->second;
}

double number(const JsonValue& value) {
  if (value.type != JsonValue::Type::number) {
    throw std::invalid_argument("expected JSON number");
  }
  return value.number;
}

std::string string(const JsonValue& value) {
  if (value.type != JsonValue::Type::string) {
    throw std::invalid_argument("expected JSON string");
  }
  return value.string;
}

bool boolean(const JsonValue& value) {
  if (value.type != JsonValue::Type::boolean) {
    throw std::invalid_argument("expected JSON boolean");
  }
  return value.boolean;
}

const std::vector<JsonValue>& array(const JsonValue& value) {
  if (value.type != JsonValue::Type::array) {
    throw std::invalid_argument("expected JSON array");
  }
  return value.array;
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
  if (value == "sidewalk") return EdgeKind::sidewalk;
  if (value == "shared_way") return EdgeKind::shared_way;
  if (value == "turn") return EdgeKind::turn;
  if (value == "crossing") return EdgeKind::crossing;
  throw std::invalid_argument("unknown edge kind: " + value);
}

}  // namespace

EngineInput parse_engine_input(std::string_view input) {
  if (input.size() > 20'000'000) {
    throw std::invalid_argument("engine input is too large");
  }
  const JsonValue root = Parser(input).parse();
  EngineInput result;
  const double version = number(field(root, "schemaVersion"));
  if (version != 2.0) {
    throw std::invalid_argument("schemaVersion must be 2");
  }
  result.origin = point(field(root, "originMeters"));
  if (const auto* value = optional_field(root, "originEdgeId")) {
    if (value->type != JsonValue::Type::null_value) {
      result.origin_edge_id = string(*value);
    }
  }
  result.threshold_seconds = number(field(root, "thresholdSeconds"));
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
  const auto& nodes = array(field(root, "nodes"));
  const auto& edges = array(field(root, "edges"));
  // The synthetic full-map fixture duplicates major-road centreline vertices
  // into separated sidewalk sides. Keep a bounded input, but allow that graph.
  if (nodes.size() > 30'000 || edges.size() > 50'000) {
    throw std::invalid_argument("walking graph exceeds engine limits");
  }
  for (const auto& value : nodes) {
    result.nodes.push_back({string(field(value, "id")), point(value)});
  }
  for (const auto& value : edges) {
    WalkEdge edge;
    edge.id = string(field(value, "id"));
    edge.from = string(field(value, "from"));
    edge.to = string(field(value, "to"));
    edge.kind = edge_kind(string(field(value, "kind")));
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
      result.service_categories.push_back({string(field(value, "id")),
          string(field(value, "dataStatus"))});
    }
  }
  validate_graph(result);
  return result;
}

}  // namespace isochrone
