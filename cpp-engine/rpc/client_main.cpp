#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

#include <grpcpp/grpcpp.h>

#include "engine_service.grpc.pb.h"
#include "rpc_options.hpp"

namespace {

void usage() {
  std::cerr << "Usage: isochrone_rpc_client compute|health [--port N]\n";
}

std::string read_request_json(std::istream& input) {
  constexpr std::size_t chunk_size = 8192;
  const auto limit = static_cast<std::size_t>(isochrone::rpc::kMaxJsonInputBytes);
  std::array<char, chunk_size> chunk{};
  std::string result;
  result.reserve(std::min(chunk_size, limit));
  while (result.size() < limit) {
    const std::size_t count = std::min(chunk.size(), limit - result.size());
    input.read(chunk.data(), static_cast<std::streamsize>(count));
    const auto received = input.gcount();
    if (received > 0) {
      result.append(chunk.data(), static_cast<std::size_t>(received));
    }
    if (input.bad()) {
      throw std::runtime_error("Cannot read request JSON from stdin.");
    }
    if (input.eof()) return result;
    if (input.fail()) {
      throw std::runtime_error("Cannot read request JSON from stdin.");
    }
  }
  // Read at most one extra byte to distinguish an exact-limit payload from an
  // oversized stream, without allocating space for the rest of that stream.
  if (input.peek() != std::char_traits<char>::eof()) {
    throw std::length_error("Request JSON exceeds the input byte limit.");
  }
  if (input.bad() || (input.fail() && !input.eof())) {
    throw std::runtime_error("Cannot read request JSON from stdin.");
  }
  return result;
}

}  // namespace

int main(int argc, char* argv[]) {
  if (argc != 2 && argc != 4) {
    usage();
    return 2;
  }
  const std::string_view command(argv[1]);
  if (command != "compute" && command != "health") {
    usage();
    return 2;
  }

  try {
    int port = isochrone::rpc::kDefaultPort;
    if (argc == 4) {
      if (std::string_view(argv[2]) != "--port") {
        usage();
        return 2;
      }
      port = isochrone::rpc::parse_port(argv[3]);
    }
    grpc::ChannelArguments arguments;
    arguments.SetMaxReceiveMessageSize(isochrone::rpc::kMaxSendMessageBytes);
    arguments.SetMaxSendMessageSize(isochrone::rpc::kMaxReceiveMessageBytes);
    const auto channel = grpc::CreateCustomChannel(
        isochrone::rpc::loopback_address(port),
        grpc::InsecureChannelCredentials(), arguments);
    auto stub = livingcircle::engine::v1::EngineService::NewStub(channel);
    grpc::ClientContext context;
    livingcircle::engine::v1::JsonReply reply;
    grpc::Status status;
    if (command == "compute") {
      livingcircle::engine::v1::ComputeRequest request;
      request.set_request_json(read_request_json(std::cin));
      context.set_deadline(std::chrono::system_clock::now() +
                           std::chrono::seconds(isochrone::rpc::kComputeDeadlineSeconds));
      status = stub->Compute(&context, request, &reply);
    } else {
      livingcircle::engine::v1::HealthRequest request;
      context.set_deadline(std::chrono::system_clock::now() +
                           std::chrono::seconds(isochrone::rpc::kHealthDeadlineSeconds));
      status = stub->Health(&context, request, &reply);
    }
    if (!status.ok()) {
      // Do not print upstream messages: they may contain request data.
      std::cerr << "RPC failed: status_code=" << static_cast<int>(status.error_code()) << '\n';
      return 1;
    }
    std::cout << reply.response_json() << '\n';
    if (!std::cout) {
      std::cerr << "Cannot write RPC reply to stdout.\n";
      return 1;
    }
    // Business success is represented by the JSON, independently of RPC OK.
    return 0;
  } catch (const std::invalid_argument&) {
    std::cerr << "Invalid RPC client arguments.\n";
    usage();
    return 2;
  } catch (const std::length_error&) {
    std::cerr << "Request JSON exceeds the input byte limit.\n";
    return 2;
  } catch (const std::exception&) {
    std::cerr << "RPC client failed.\n";
    return 1;
  }
}
