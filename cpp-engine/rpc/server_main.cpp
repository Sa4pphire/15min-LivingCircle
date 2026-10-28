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

#include <grpcpp/grpcpp.h>

#include "engine_service.hpp"
#include "rpc_options.hpp"

namespace {

void usage() {
  std::cerr << "Usage: isochrone_rpc_server [--port PORT] [--compute-capacity N]\n";
}

struct ServerOptions {
  int port{isochrone::rpc::kDefaultPort};
  int compute_capacity{isochrone::rpc::kDefaultComputeCapacity};
};

ServerOptions server_options(int argc, char* argv[]) {
  ServerOptions options;
  bool has_port = false;
  bool has_compute_capacity = false;
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
    } else {
      throw std::invalid_argument("Unknown, duplicate or incomplete server option.");
    }
  }
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
    isochrone::rpc::EngineServiceImpl service(options.compute_capacity);
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
    std::osyncstream(std::cerr) << "rpc_server event=stopped\n";
    return wait_error == 0 ? 0 : 1;
  } catch (const std::exception&) {
    std::cerr << "rpc_server event=failed\n";
    return 1;
  }
}
