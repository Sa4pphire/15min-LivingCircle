#include "engine_service.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <syncstream>
#include <utility>

#include "isochrone/execution_control.hpp"
#include "isochrone/json_io.hpp"
#include "rpc_options.hpp"

namespace isochrone::rpc {
namespace {

using JsonReply = ::livingcircle::engine::v1::JsonReply;
using GraphComputeReply = ::livingcircle::engine::v1::GraphComputeReply;
using SteadyClock = std::chrono::steady_clock;

// Logging never exposes payloads or exception details and cannot prevent
// handler cleanup if the output stream itself fails.
class CallLog {
 public:
  explicit CallLog(const char* method) noexcept : method_(method) {}

  ~CallLog() noexcept {
    try {
      const double elapsed_ms = std::chrono::duration<double, std::milli>(
          SteadyClock::now() - started_).count();
      std::osyncstream(std::cerr)
          << "engine_rpc method=" << method_
          << " status=" << static_cast<int>(status_)
          << " duration_ms=" << elapsed_ms
          << " business_code=" << business_code_ << '\n';
    } catch (...) {
      // A logging failure must not escape a handler or its destructor.
    }
  }

  ::grpc::Status Finish(::grpc::Status status,
                        const char* business_code = "-") {
    status_ = status.error_code();
    business_code_ = business_code;
    return status;
  }

 private:
  const char* method_;
  SteadyClock::time_point started_{SteadyClock::now()};
  ::grpc::StatusCode status_{::grpc::StatusCode::INTERNAL};
  const char* business_code_{"-"};
};

// The permit covers application parsing, CPU work, JSON serialization and
// reply construction. gRPC serializes/sends the protobuf after the handler
// returns, so this is not a bound on the transport's complete lifetime.
class ComputePermit {
 public:
  ComputePermit() noexcept = default;

  ~ComputePermit() noexcept {
    if (gate_) gate_->Release();
  }

  ComputePermit(const ComputePermit&) = delete;
  ComputePermit& operator=(const ComputePermit&) = delete;

  [[nodiscard]] bool TryAcquire(ComputeGate& gate) noexcept {
    if (!gate.TryAcquire()) return false;
    gate_ = &gate;
    return true;
  }

 private:
  ComputeGate* gate_{};
};

std::optional<StopReason> ReadStopReason(::grpc::ServerContext& context) {
  if (std::chrono::system_clock::now() >= context.deadline()) {
    return StopReason::DeadlineExceeded;
  }
  if (context.IsCancelled()) {
    return StopReason::Cancelled;
  }
  return std::nullopt;
}

::grpc::Status StatusForStop(StopReason reason) {
  if (reason == StopReason::DeadlineExceeded) {
    return {::grpc::StatusCode::DEADLINE_EXCEEDED, "Request deadline exceeded."};
  }
  return {::grpc::StatusCode::CANCELLED, "Request was cancelled."};
}

std::optional<::grpc::Status> CancellationStatus(
    ::grpc::ServerContext& context) {
  if (const auto reason = ReadStopReason(context)) return StatusForStop(*reason);
  return std::nullopt;
}

std::optional<::grpc::Status> AdmissionStatus(
    ::grpc::ServerContext& context, const std::atomic<bool>& draining) {
  if (auto status = CancellationStatus(context)) return status;
  if (draining.load(std::memory_order_acquire)) {
    return ::grpc::Status(::grpc::StatusCode::UNAVAILABLE,
                          "Engine service is shutting down.");
  }
  return std::nullopt;
}

template <typename Reply>
::grpc::Status SetReply(::grpc::ServerContext& context, Reply& response,
                        std::string json) {
  if (auto status = CancellationStatus(context)) {
    response.Clear();
    return std::move(*status);
  }
  if (json.size() > static_cast<std::size_t>(kMaxSendMessageBytes)) {
    response.Clear();
    context.AddTrailingMetadata("engine-error-code", "ENGINE_RESPONSE_TOO_LARGE");
    return {::grpc::StatusCode::RESOURCE_EXHAUSTED,
            "Response exceeds configured message limit."};
  }
  response.set_response_json(std::move(json));
  // Count the complete protobuf, including graph identity/health metadata as
  // well as the bytes-field tag and length. Preserve metadata on success.
  if (response.ByteSizeLong() >
      static_cast<std::size_t>(kMaxSendMessageBytes)) {
    response.Clear();
    context.AddTrailingMetadata("engine-error-code", "ENGINE_RESPONSE_TOO_LARGE");
    return {::grpc::StatusCode::RESOURCE_EXHAUSTED,
            "Response exceeds configured message limit."};
  }
  if (auto status = CancellationStatus(context)) {
    response.Clear();
    return std::move(*status);
  }
  return ::grpc::Status::OK;
}

struct BusinessReply {
  std::string json;
  const char* code{"-"};
};

// Both input shapes keep the same parsing/compute/serialization boundaries and
// business-error envelope. Selecting a snapshot happens outside this helper:
// graph identity errors are RPC precondition failures, not JSON business errors.
template <typename Parse, typename Compute>
BusinessReply BuildBusinessReply(const std::string& payload,
                                 const ExecutionControl& control,
                                 Parse parse, Compute compute) {
  BusinessReply reply;
  try {
    control.Checkpoint();
    if (!has_non_whitespace(payload)) {
      throw std::invalid_argument("Engine input must be a JSON object.");
    }
    if (payload.size() > kMaxJsonInputBytes) {
      throw std::invalid_argument("engine input is too large");
    }
    const auto parse_started = SteadyClock::now();
    const auto input = parse(payload);
    const double input_parse_ms = std::chrono::duration<double, std::milli>(
        SteadyClock::now() - parse_started).count();
    control.Checkpoint();

    // Parsing and serialization observe cancellation at stage boundaries;
    // the computation body also checks during its CPU work.
    EngineResult result = compute(input, control);
    control.Checkpoint();
    result.stage_timings_ms.emplace_back("inputParse", input_parse_ms);
    reply.json = serialize_engine_result(result);
    control.Checkpoint();
  } catch (const ExecutionInterrupted&) {
    throw;
  } catch (const OriginNotOnWalkway& error) {
    reply.code = "ORIGIN_NOT_ON_WALKWAY";
    reply.json = error_json(reply.code, error.what());
  } catch (const AmbiguousOriginSide& error) {
    reply.code = "AMBIGUOUS_ORIGIN_SIDE";
    reply.json = error_json(reply.code, error.what());
  } catch (const std::out_of_range& error) {
    reply.code = "INVALID_INPUT";
    reply.json = error_json(reply.code, error.what());
  } catch (const std::invalid_argument& error) {
    reply.code = "INVALID_INPUT";
    reply.json = error_json(reply.code, error.what());
  } catch (const std::exception&) {
    reply.code = "ENGINE_ERROR";
    reply.json = error_json(reply.code, "Calculation failed.");
  }
  return reply;
}

// The two public compute methods pass the same service-owned gate here. This
// helper owns the entire application lifetime, including error replies, so an
// early stop or failed graph selection cannot release a still-used permit.
template <typename Reply, typename Work>
::grpc::Status RunComputeCall(const char* method, ::grpc::ServerContext& context,
                              Reply& response, const std::atomic<bool>& draining,
                              ComputeGate& computing, Work work) {
  CallLog log(method);
  // Keep the guard outside the try block so even an error-response failure
  // retains the permit until the final INTERNAL response has been prepared.
  ComputePermit permit;
  try {
    response.Clear();
    if (auto status = AdmissionStatus(context, draining)) {
      return log.Finish(std::move(*status));
    }
    if (!permit.TryAcquire(computing)) {
      context.AddTrailingMetadata("engine-error-code", "ENGINE_BUSY");
      return log.Finish(
          {::grpc::StatusCode::RESOURCE_EXHAUSTED, "Engine is busy."},
          "ENGINE_BUSY");
    }
    // Drain/cancellation can race the initial check and the nonblocking gate.
    if (auto status = AdmissionStatus(context, draining)) {
      return log.Finish(std::move(*status));
    }

    // This callback only lives for the synchronous handler. Draining is not
    // part of the probe: accepted work retains the existing shutdown grace.
    const ExecutionControl control([&context] { return ReadStopReason(context); });
    BusinessReply reply = work(control);

    // The guard survives stack unwinding and response construction. Detecting
    // a stop never frees the slot before the owned application work is gone.
    return log.Finish(SetReply(context, response, std::move(reply.json)), reply.code);
  } catch (const GraphSelectionError& error) {
    response.Clear();
    const char* code = "GRAPH_VERSION_MISMATCH";
    ::grpc::StatusCode status = ::grpc::StatusCode::FAILED_PRECONDITION;
    if (error.reason() == GraphSelectionFailure::InvalidId) {
      code = "INVALID_GRAPH_ID";
      status = ::grpc::StatusCode::INVALID_ARGUMENT;
    } else if (error.reason() == GraphSelectionFailure::NotLoaded) {
      code = "GRAPH_NOT_LOADED";
    }
    context.AddTrailingMetadata("engine-error-code", code);
    return log.Finish({status, error.what()}, code);
  } catch (const ExecutionInterrupted& stopped) {
    response.Clear();
    return log.Finish(StatusForStop(stopped.reason()));
  } catch (...) {
    response.Clear();
    return log.Finish({::grpc::StatusCode::INTERNAL, "RPC failed."},
                      "ENGINE_ERROR");
  }
}

}  // namespace

EngineServiceImpl::EngineServiceImpl(
    int compute_capacity, std::unique_ptr<const GraphSnapshot> snapshot)
    : graphs_(std::move(snapshot)), computing_(compute_capacity) {}

void EngineServiceImpl::BeginDrain() noexcept {
  // Retain the snapshot until every borrowing handler has finished.
  draining_.store(true, std::memory_order_release);
}

::grpc::Status EngineServiceImpl::Compute(
    ::grpc::ServerContext* context,
    const ::livingcircle::engine::v1::ComputeRequest* request,
    JsonReply* response) {
  return RunComputeCall("Compute", *context, *response, draining_, computing_,
      [&](const ExecutionControl& control) {
        return BuildBusinessReply(request->request_json(), control, parse_engine_input,
            [](const EngineInput& input, const ExecutionControl& execution) {
              return compute_reachability(input.graph, input.request, execution);
            });
      });
}

::grpc::Status EngineServiceImpl::ComputeOnGraph(
    ::grpc::ServerContext* context,
    const ::livingcircle::engine::v1::GraphComputeRequest* request,
    GraphComputeReply* response) {
  return RunComputeCall("ComputeOnGraph", *context, *response, draining_, computing_,
      [&](const ExecutionControl& control) {
        control.Checkpoint();
        // Selection errors bypass the JSON business-error envelope. The store
        // never loads or substitutes a graph while processing a request.
        const GraphSnapshot& snapshot = graphs_.require(request->graph_id());
        response->set_graph_id(snapshot.id());
        return BuildBusinessReply(request->analysis_request_json(), control,
            parse_analysis_request,
            [&snapshot](const AnalysisRequest& input, const ExecutionControl& execution) {
              return compute_reachability(snapshot, input, execution);
            });
      });
}

::grpc::Status EngineServiceImpl::Health(
    ::grpc::ServerContext* context,
    const ::livingcircle::engine::v1::HealthRequest*, JsonReply* response) {
  CallLog log("Health");
  try {
    response->Clear();
    if (auto status = AdmissionStatus(*context, draining_)) {
      return log.Finish(std::move(*status));
    }
    // Health has no compute permit and remains independent of busy CPU work.
    if (const GraphSnapshot* snapshot = graphs_.snapshot()) {
      const auto& stats = snapshot->stats();
      auto* description = response->mutable_graph_snapshot();
      description->set_graph_id(snapshot->id());
      description->set_source_bytes(static_cast<std::uint64_t>(stats.source_bytes));
      description->set_node_count(static_cast<std::uint64_t>(stats.node_count));
      description->set_edge_count(static_cast<std::uint64_t>(stats.edge_count));
      description->set_path_point_count(static_cast<std::uint64_t>(stats.path_point_count));
    }
    auto status = SetReply(*context, *response, health_json());
    if (status.ok()) {
      if (auto admission = AdmissionStatus(*context, draining_)) {
        response->Clear();
        return log.Finish(std::move(*admission));
      }
    }
    return log.Finish(std::move(status));
  } catch (...) {
    response->Clear();
    return log.Finish({::grpc::StatusCode::INTERNAL, "RPC failed."},
                      "ENGINE_ERROR");
  }
}

}  // namespace isochrone::rpc
