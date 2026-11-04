#include <pthread.h>
#include <signal.h>

#include <chrono>
#include <exception>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <syncstream>
#include <utility>

#include <grpcpp/grpcpp.h>

#include "engine_service.hpp"
#include "isochrone/graph_identity.hpp"
#include "isochrone/graph_snapshot.hpp"
#include "rpc_options.hpp"

#if defined(ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER) && ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER
#include "isochrone/graph_snapshot_loader.hpp"
#endif

namespace {

void usage() {
  std::cerr << "Usage: isochrone_rpc_server [--port PORT] [--compute-capacity N] "
               "[--graph-artifact PATH --graph-id sha256:HEX]\n";
}

struct ServerOptions {
  int port{isochrone::rpc::kDefaultPort};
  int compute_capacity{isochrone::rpc::kDefaultComputeCapacity};
  std::string graph_artifact;
  std::string graph_id;
};

ServerOptions server_options(int argc, char* argv[]) {
  ServerOptions options;
  bool has_port = false;
  bool has_compute_capacity = false;
  bool has_graph_artifact = false;
  bool has_graph_id = false;
  for (int index = 1; index < argc; ++index) {
    const std::string_view option(argv[index]);
    if (option == "--port" && !has_port && index + 1 < argc) {
      options.port = isochrone::rpc::parse_port(argv[++index]);
      has_port = true;
    } else if (option == "--compute-capacity" && !has_compute_capacity &&
               index + 1 < argc) {
      options.compute_capacity =
          isochrone::rpc::parse_compute_capacity(argv[++index]);
      has_compute_capacity = true;
    } else if (option == "--graph-artifact" && !has_graph_artifact &&
               index + 1 < argc) {
      options.graph_artifact = argv[++index];
      if (options.graph_artifact.empty() || options.graph_artifact.starts_with("--")) {
        throw std::invalid_argument("--graph-artifact requires a non-empty path.");
      }
      has_graph_artifact = true;
    } else if (option == "--graph-id" && !has_graph_id && index + 1 < argc) {
      options.graph_id = argv[++index];
      if (!isochrone::is_graph_snapshot_id(options.graph_id)) {
        throw std::invalid_argument("--graph-id requires sha256:<64 lowercase hex digits>.");
      }
      has_graph_id = true;
    } else {
      throw std::invalid_argument("Unknown, duplicate or incomplete server option.");
    }
  }
  if (has_graph_artifact != has_graph_id) {
    throw std::invalid_argument("--graph-artifact and --graph-id must be provided together.");
  }
#if !defined(ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER) || !ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER
  if (has_graph_artifact) {
    throw std::invalid_argument(
        "This build has no graph snapshot loader; enable BUILD_GRAPH_SNAPSHOT_LOADER.");
  }
#endif
  return options;
}

}  // namespace

int main(int argc, char* argv[]) {
  ServerOptions options;
  try {
    options = server_options(argc, argv);
  } catch (const std::invalid_argument& error) {
    usage();
    std::cerr << error.what() << '\n';
    return 2;
  }

  // Block before constructing gRPC objects or starting its worker threads.
  // They inherit this mask, leaving the main thread to receive termination
  // through sigwait; no gRPC operation runs in an asynchronous signal handler.
  sigset_t termination_signals;
  if (sigemptyset(&termination_signals) != 0 ||
      sigaddset(&termination_signals, SIGINT) != 0 ||
      sigaddset(&termination_signals, SIGTERM) != 0) {
    std::cerr << "rpc_server event=signal_set_failed\n";
    return 1;
  }
  const int mask_error =
      pthread_sigmask(SIG_BLOCK, &termination_signals, nullptr);
  if (mask_error != 0) {
    std::cerr << "rpc_server event=signal_mask_failed code=" << mask_error << '\n';
    return 1;
  }

  try {
    std::unique_ptr<const isochrone::GraphSnapshot> snapshot;
#if defined(ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER) && ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER
    if (!options.graph_artifact.empty()) {
      const auto load_started = std::chrono::steady_clock::now();
      try {
        snapshot = isochrone::GraphSnapshotLoader::load(
            options.graph_artifact, options.graph_id);
      } catch (const std::exception&) {
        // Loading must finish before constructing a builder or listening.
        // Keep paths, file content and upstream exception text out of logs.
        std::cerr << "rpc_server event=graph_load_failed\n";
        return 1;
      }
      const double load_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - load_started).count();
      const auto& stats = snapshot->stats();
      std::osyncstream(std::cerr)
          << "rpc_server event=graph_loaded graph_id=" << snapshot->id()
          << " source_bytes=" << stats.source_bytes
          << " node_count=" << stats.node_count
          << " edge_count=" << stats.edge_count
          << " path_point_count=" << stats.path_point_count
          << " load_ms=" << load_ms << '\n';
    }
#endif
    // The service owns the snapshot and is constructed before its borrowing
    // server. No request can observe a partially loaded graph.
    isochrone::rpc::EngineServiceImpl service(
        options.compute_capacity, std::move(snapshot));
    grpc::ServerBuilder builder;
    const std::string address = isochrone::rpc::loopback_address(options.port);
    int selected_port{};
    builder.AddListeningPort(address, grpc::InsecureServerCredentials(),
                             &selected_port);
    builder.RegisterService(&service);
    builder.SetMaxReceiveMessageSize(isochrone::rpc::kMaxReceiveMessageBytes);
    builder.SetMaxSendMessageSize(isochrone::rpc::kMaxSendMessageBytes);
    // Pollers are framework reception capacity, not the application compute
    // limit. The service owns a separate, non-blocking compute-capacity gate.
    builder.SetSyncServerOption(grpc::ServerBuilder::NUM_CQS, 1);
    builder.SetSyncServerOption(grpc::ServerBuilder::MIN_POLLERS, 2);
    builder.SetSyncServerOption(grpc::ServerBuilder::MAX_POLLERS, 2);
    std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
    if (!server) {
      std::cerr << "rpc_server event=start_failed\n";
      return 1;
    }

    std::osyncstream(std::cerr)
        << "rpc_server event=started address=" << address
        << " selected_port=" << selected_port
        << " compute_capacity=" << options.compute_capacity << '\n';
    int received_signal{};
    const int wait_error = sigwait(&termination_signals, &received_signal);
    service.BeginDrain();
    std::osyncstream(std::cerr)
        << "rpc_server event=draining signal=" << received_signal
        << " wait_error=" << wait_error << '\n';

    // After the grace period, gRPC cancellation reaches computational
    // checkpoints through the handler's probe. Parsing, serialization, library
    // operations and cleanup can still delay exit; this is not a hard CPU kill.
    server->Shutdown(std::chrono::system_clock::now() +
                     std::chrono::seconds(isochrone::rpc::kShutdownGraceSeconds));
    server->Wait();
    // Destroy framework users before service/store release the fixed graph.
    server.reset();
    std::osyncstream(std::cerr) << "rpc_server event=stopped\n";
    return wait_error == 0 ? 0 : 1;
  } catch (const std::exception&) {
    std::cerr << "rpc_server event=failed\n";
    return 1;
  }
}
