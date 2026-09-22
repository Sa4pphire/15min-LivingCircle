#include <iostream>
#include <string_view>

#include "isochrone/json_io.hpp"

int main(int argc, char* argv[]) {
  if (argc == 2 && std::string_view(argv[1]) == "--health") {
    std::cout << isochrone::health_json() << '\n';
    return 0;
  }

  const std::string input = isochrone::read_all(std::cin);
  if (!isochrone::has_non_whitespace(input)) {
    std::cout << isochrone::error_json("INVALID_INPUT",
                                        "Engine input must be a JSON object.")
              << '\n';
    return 2;
  }

  std::cout << isochrone::error_json(
                   "NOT_IMPLEMENTED",
                   "JSON contract parsing and the full pipeline are the next task.")
            << '\n';
  return 4;
}
