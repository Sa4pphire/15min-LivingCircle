#include <cmath>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

#include "isochrone/json_io.hpp"

#ifndef ISOCHRONE_SYNTHETIC_NETWORK_PATH
#define ISOCHRONE_SYNTHETIC_NETWORK_PATH "data/networks/synthetic-preview.json"
#endif

namespace {

double meter_argument(const char* argument) {
  const std::string text(argument);
  std::size_t consumed{};
  const double value = std::stod(text, &consumed);
  if (consumed != text.size() || !std::isfinite(value)) {
    throw std::invalid_argument("Origin coordinates must be finite local meters.");
  }
  return value;
}

}  // namespace

int main(int argc, char* argv[]) {
  if (argc == 2 && std::string_view(argv[1]) == "--health") {
    std::cout << isochrone::health_json() << '\n';
    return 0;
  }

  try {
    std::string file_path;
    bool network_file = false;
    bool local_experiment = false;
    bool has_origin = false;
    isochrone::Point origin{};
    std::optional<std::string> origin_edge_id;
    for (int index = 1; index < argc; ++index) {
      const std::string_view option(argv[index]);
      if (option == "--demo" && file_path.empty()) {
        const char* configured = std::getenv("SYNTHETIC_NETWORK_PATH");
        file_path = configured && *configured ? configured : ISOCHRONE_SYNTHETIC_NETWORK_PATH;
        network_file = true;
      } else if ((option == "--input" || option == "--network") &&
                 file_path.empty() && index + 1 < argc) {
        network_file = option == "--network";
        file_path = argv[++index];
        if (file_path.empty()) throw std::invalid_argument("Input file path is empty.");
      } else if (option == "--origin-meters" && !has_origin && index + 2 < argc) {
        origin.x = meter_argument(argv[++index]);
        origin.y = meter_argument(argv[++index]);
        has_origin = true;
      } else if (option == "--origin-edge" && !origin_edge_id && index + 1 < argc) {
        origin_edge_id = argv[++index];
      } else if (option == "--local" && !local_experiment) {
        local_experiment = true;
      } else {
        throw std::invalid_argument("Unknown, duplicate or incomplete command-line option.");
      }
    }
    if (!network_file && (has_origin || origin_edge_id || local_experiment)) {
      throw std::invalid_argument("Origin overrides and --local require --demo or --network.");
    }
    std::string input;
    if (file_path.empty()) {
      input = isochrone::read_all(std::cin);
    } else {
      std::ifstream file(file_path, std::ios::binary);
      if (!file) {
        std::cout << isochrone::error_json("INPUT_FILE_ERROR", "Cannot open input file.")
                  << '\n';
        std::cerr << "Cannot open input file: " << file_path << '\n';
        return 2;
      }
      input = isochrone::read_all(file);
      if (file.bad()) throw std::runtime_error("Failed to read input file.");
    }
    if (!isochrone::has_non_whitespace(input)) {
      throw std::invalid_argument("Engine input must be a JSON object.");
    }
    const auto parse_started = std::chrono::steady_clock::now();
    const isochrone::EngineInput request = network_file
        ? isochrone::parse_synthetic_network(input, origin, origin_edge_id, local_experiment)
        : isochrone::parse_engine_input(input);
    const double input_parse_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - parse_started).count();
    isochrone::EngineResult result =
        isochrone::compute_reachability(request);
    result.stage_timings_ms.emplace_back("inputParse", input_parse_ms);
    if (network_file) {
      result.warnings.push_back("SYNTHETIC_NETWORK_NOT_REAL_WORLD");
      if (local_experiment && request.service_categories.empty()) {
        result.warnings.push_back("LOCAL_FACILITY_DATA_NOT_PROVIDED");
      }
      if (local_experiment && request.local_experiment->topology_status != "verified" &&
          request.local_experiment->boundary_node_ids.empty()) {
        result.warnings.push_back("LOCAL_BOUNDARY_NOT_MARKED");
      }
    }
    std::cout << isochrone::serialize_engine_result(result) << '\n';
    return 0;
  } catch (const isochrone::OriginNotOnWalkway& error) {
    std::cout << isochrone::error_json("ORIGIN_NOT_ON_WALKWAY", error.what())
              << '\n';
    return 3;
  } catch (const isochrone::AmbiguousOriginSide& error) {
    std::cout << isochrone::error_json("AMBIGUOUS_ORIGIN_SIDE", error.what())
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
