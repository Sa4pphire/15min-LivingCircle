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
#include "isochrone/graph_identity.hpp"
#include "rpc_options.hpp"

namespace {

void usage() {
  std::cerr << "Usage: isochrone_rpc_client compute|health [--port N]\n"
               "       isochrone_rpc_client compute-on-graph --graph-id sha256:HEX [--port N]\n";
}

struct ClientOptions {
  std::string_view command;
  int port{isochrone::rpc::kDefaultPort};
  std::string graph_id;
};

ClientOptions client_options(int argc, char* argv[]) {
  if (argc < 2) throw std::invalid_argument("Missing RPC command.");
  ClientOptions options;
  options.command = argv[1];
  if (options.command != "compute" && options.command != "health" &&
      options.command != "compute-on-graph") {
    throw std::invalid_argument("Unknown RPC command.");
  }
  bool has_port = false;
  bool has_graph_id = false;
  for (int index = 2; index < argc; ++index) {
    const std::string_view option(argv[index]);
    if (option == "--port" && !has_port && index + 1 < argc) {
      options.port = isochrone::rpc::parse_port(argv[++index]);
      has_port = true;
    } else if (option == "--graph-id" && !has_graph_id && index + 1 < argc) {
      options.graph_id = argv[++index];
      if (!isochrone::is_graph_snapshot_id(options.graph_id)) {
        throw std::invalid_argument("Invalid graph identity.");
      }
      has_graph_id = true;
    } else {
      throw std::invalid_argument("Unknown, duplicate or incomplete client option.");
    }
  }
  if ((options.command == "compute-on-graph") != has_graph_id) {
    throw std::invalid_argument("--graph-id is required only for compute-on-graph.");
  }
  return options;
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
  try {
    const ClientOptions options = client_options(argc, argv);
    grpc::ChannelArguments arguments;
    arguments.SetMaxReceiveMessageSize(isochrone::rpc::kMaxSendMessageBytes);
    arguments.SetMaxSendMessageSize(isochrone::rpc::kMaxReceiveMessageBytes);
    const auto channel = grpc::CreateCustomChannel(
        isochrone::rpc::loopback_address(options.port),
        grpc::InsecureChannelCredentials(), arguments);
    auto stub = livingcircle::engine::v1::EngineService::NewStub(channel);
    grpc::ClientContext context;
    livingcircle::engine::v1::JsonReply reply;
    livingcircle::engine::v1::GraphComputeReply graph_reply;
    grpc::Status status;
    if (options.command == "compute") {
      livingcircle::engine::v1::ComputeRequest request;
      request.set_request_json(read_request_json(std::cin));
      if (request.ByteSizeLong() >
          static_cast<std::size_t>(isochrone::rpc::kMaxReceiveMessageBytes)) {
        throw std::length_error("Request protobuf exceeds the message limit.");
      }
      context.set_deadline(std::chrono::system_clock::now() +
                           std::chrono::seconds(isochrone::rpc::kComputeDeadlineSeconds));
      status = stub->Compute(&context, request, &reply);
    } else if (options.command == "compute-on-graph") {
      livingcircle::engine::v1::GraphComputeRequest request;
      request.set_graph_id(options.graph_id);
      request.set_analysis_request_json(read_request_json(std::cin));
      // Include graph ID, field tags and lengths in the transport size check.
      if (request.ByteSizeLong() >
          static_cast<std::size_t>(isochrone::rpc::kMaxReceiveMessageBytes)) {
        throw std::length_error("Request protobuf exceeds the message limit.");
      }
      context.set_deadline(std::chrono::system_clock::now() +
                           std::chrono::seconds(isochrone::rpc::kComputeDeadlineSeconds));
      status = stub->ComputeOnGraph(&context, request, &graph_reply);
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
    if (options.command == "compute-on-graph" &&
        graph_reply.graph_id() != options.graph_id) {
      std::cerr << "RPC reply graph identity does not match the request.\n";
      return 1;
    }
    if (options.command == "health" && reply.has_graph_snapshot()) {
      const auto& graph = reply.graph_snapshot();
      if (!isochrone::is_graph_snapshot_id(graph.graph_id())) {
        std::cerr << "RPC health reply contains an invalid graph identity.\n";
        return 1;
      }
      std::cerr << "Loaded graph: graph_id=" << graph.graph_id()
                << " nodes=" << graph.node_count()
                << " edges=" << graph.edge_count()
                << " source_bytes=" << graph.source_bytes() << '\n';
    }
    const std::string& response_json = options.command == "compute-on-graph"
        ? graph_reply.response_json() : reply.response_json();
    std::cout << response_json << '\n';
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
    std::cerr << "Request exceeds the configured input or message byte limit.\n";
    return 2;
  } catch (const std::exception&) {
    std::cerr << "RPC client failed.\n";
    return 1;
  }
}
