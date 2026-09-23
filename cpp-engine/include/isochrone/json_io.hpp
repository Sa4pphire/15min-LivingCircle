#pragma once

#include <istream>
#include <string>
#include <string_view>

#include "isochrone/engine.hpp"

namespace isochrone {

[[nodiscard]] std::string read_all(std::istream& input);
[[nodiscard]] bool has_non_whitespace(std::string_view input);
[[nodiscard]] std::string health_json();
[[nodiscard]] std::string error_json(std::string_view code,
                                     std::string_view message);

[[nodiscard]] EngineInput parse_engine_input(std::string_view input);
[[nodiscard]] std::string serialize_engine_result(const EngineResult& result);

}  // namespace isochrone
