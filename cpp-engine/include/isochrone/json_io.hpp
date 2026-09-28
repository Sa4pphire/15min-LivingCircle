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
// Load the existing synthetic graph without adding nodes, edges or facilities.
// Runtime coordinates are local meters; Python remains responsible for map coordinates.
[[nodiscard]] EngineInput parse_synthetic_network(
    std::string_view input, Point origin,
    std::optional<std::string> origin_edge_id = std::nullopt,
    bool local_experiment = false);
[[nodiscard]] std::string serialize_engine_result(const EngineResult& result);

}  // namespace isochrone
