#include <iostream>
#include <sstream>
#include <string>
#include <string_view>

namespace {
constexpr std::string_view kHealthJson =
    R"({"status":"ok","engine":"isochrone_engine","schemaVersion":1})";

constexpr std::string_view kNotImplementedJson =
    R"({"success":false,"error":{"code":"NOT_IMPLEMENTED","message":"Isochrone calculation is not implemented yet."}})";
}  // namespace

int main(int argc, char* argv[]) {
  if (argc == 2 && std::string_view(argv[1]) == "--health") {
    std::cout << kHealthJson << '\n';
    return 0;
  }

  std::ostringstream input;
  input << std::cin.rdbuf();
  if (input.str().empty()) {
    std::cerr << "engine input must be a JSON object\n";
    return 2;
  }

  std::cout << kNotImplementedJson << '\n';
  return 4;
}
