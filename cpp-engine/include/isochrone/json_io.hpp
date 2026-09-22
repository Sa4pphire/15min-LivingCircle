#pragma once

#include <istream>
#include <string>
#include <string_view>

namespace isochrone {

[[nodiscard]] std::string read_all(std::istream& input);
[[nodiscard]] bool has_non_whitespace(std::string_view input);
[[nodiscard]] std::string health_json();
[[nodiscard]] std::string error_json(std::string_view code,
                                     std::string_view message);

}  // namespace isochrone
