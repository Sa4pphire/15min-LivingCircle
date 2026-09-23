#include <iostream>
#include <stdexcept>
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

  try {
    const isochrone::EngineInput request = isochrone::parse_engine_input(input);
    const isochrone::EngineResult result =
        isochrone::compute_reachability(request);
    std::cout << isochrone::serialize_engine_result(result) << '\n';
    return 0;
  } catch (const isochrone::OriginNotOnWalkway& error) {
    std::cout << isochrone::error_json("ORIGIN_NOT_ON_WALKWAY", error.what())
              << '\n';
    return 3;
  } catch (const std::out_of_range& error) {
    std::cout << isochrone::error_json("INVALID_INPUT", error.what()) << '\n';
    return 2;
  } catch (const std::invalid_argument& error) {
    std::cout << isochrone::error_json("INVALID_INPUT", error.what()) << '\n';
    return 2;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    std::cout << isochrone::error_json("ENGINE_ERROR", "Calculation failed.")
              << '\n';
    return 1;
  }
}
