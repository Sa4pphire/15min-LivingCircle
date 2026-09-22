#include "isochrone/json_io.hpp"

#include <cctype>
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
        escaped += character;
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
  return R"({"status":"ok","engine":"isochrone_engine","schemaVersion":1})";
}

std::string error_json(const std::string_view code,
                       const std::string_view message) {
  return "{\"schemaVersion\":1,\"success\":false,\"error\":{\"code\":\"" +
         escape_json(code) + "\",\"message\":\"" + escape_json(message) +
         "\"}}";
}

}  // namespace isochrone
