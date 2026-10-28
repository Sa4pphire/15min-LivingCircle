#pragma once

#include <charconv>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

namespace isochrone::rpc {

inline constexpr int kDefaultPort = 50051;
inline constexpr int kDefaultComputeCapacity = 2;
inline constexpr int kMaxReceiveMessageBytes = 20 * 1024 * 1024;
inline constexpr int kMaxSendMessageBytes = 64 * 1024 * 1024;
inline constexpr std::size_t kMaxJsonInputBytes = 20'000'000;
inline constexpr int kComputeDeadlineSeconds = 25;
inline constexpr int kHealthDeadlineSeconds = 3;
inline constexpr int kShutdownGraceSeconds = 5;

[[nodiscard]] inline int parse_port(std::string_view value) {
  if (value.empty()) {
    throw std::invalid_argument("--port requires an integer from 1 to 65535.");
  }
  int port{};
  const auto [end, error] =
      std::from_chars(value.data(), value.data() + value.size(), port);
  if (error != std::errc{} || end != value.data() + value.size() ||
      port < 1 || port > 65535) {
    throw std::invalid_argument("--port requires an integer from 1 to 65535.");
  }
  return port;
}

[[nodiscard]] inline std::string loopback_address(int port) {
  return "127.0.0.1:" + std::to_string(port);
}

[[nodiscard]] inline int parse_compute_capacity(std::string_view value) {
  const std::string error_message =
      "--compute-capacity requires an integer from 1 to " +
      std::to_string(std::numeric_limits<int>::max()) + ".";
  if (value.empty()) {
    throw std::invalid_argument(error_message);
  }
  int capacity{};
  const auto [end, error] =
      std::from_chars(value.data(), value.data() + value.size(), capacity);
  if (error != std::errc{} || end != value.data() + value.size() ||
      capacity < 1) {
    throw std::invalid_argument(error_message);
  }
  return capacity;
}

}  // namespace isochrone::rpc
