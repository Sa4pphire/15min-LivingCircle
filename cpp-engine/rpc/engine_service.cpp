#include "engine_service.hpp"

#include <chrono>
#include <cstddef>
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

::grpc::Status SetReply(::grpc::ServerContext& context, JsonReply& response,
                        std::string json) {
  if (auto status = CancellationStatus(context)) return std::move(*status);
  if (json.size() > static_cast<std::size_t>(kMaxSendMessageBytes)) {
    response.clear_response_json();
    context.AddTrailingMetadata("engine-error-code", "ENGINE_RESPONSE_TOO_LARGE");
    return {::grpc::StatusCode::RESOURCE_EXHAUSTED,
            "Response exceeds configured message limit."};
  }
  response.set_response_json(std::move(json));
  // The bytes-field tag and length also count toward the protobuf limit.
  if (response.ByteSizeLong() >
      static_cast<std::size_t>(kMaxSendMessageBytes)) {
    response.clear_response_json();
    context.AddTrailingMetadata("engine-error-code", "ENGINE_RESPONSE_TOO_LARGE");
    return {::grpc::StatusCode::RESOURCE_EXHAUSTED,
            "Response exceeds configured message limit."};
  }
  if (auto status = CancellationStatus(context)) {
    response.clear_response_json();
    return std::move(*status);
  }
  return ::grpc::Status::OK;
}

}  // namespace

EngineServiceImpl::EngineServiceImpl(int compute_capacity)
    : computing_(compute_capacity) {}

void EngineServiceImpl::BeginDrain() noexcept {
  draining_.store(true, std::memory_order_release);
}

::grpc::Status EngineServiceImpl::Compute(
    ::grpc::ServerContext* context,
    const ::livingcircle::engine::v1::ComputeRequest* request,
    JsonReply* response) {
  CallLog log("Compute");
  // Keep the guard outside the try block so even an error-response failure
  // retains the permit until the final INTERNAL response has been prepared.
  ComputePermit permit;
  try {
    response->clear_response_json();
    if (auto status = AdmissionStatus(*context, draining_)) {
      return log.Finish(std::move(*status));
    }
    if (!permit.TryAcquire(computing_)) {
      context->AddTrailingMetadata("engine-error-code", "ENGINE_BUSY");
      return log.Finish(
          {::grpc::StatusCode::RESOURCE_EXHAUSTED, "Engine is busy."},
          "ENGINE_BUSY");
    }
    // Drain/cancellation can race the initial check and the nonblocking gate.
    if (auto status = AdmissionStatus(*context, draining_)) {
      return log.Finish(std::move(*status));
    }

    // This callback only lives for the synchronous handler. Draining is not
    // part of the probe: accepted work retains the existing shutdown grace.
    const ExecutionControl control([context] { return ReadStopReason(*context); });
    std::string json;
    const char* business_code = "-";
    try {
      control.Checkpoint();
      const std::string& payload = request->request_json();
      if (!has_non_whitespace(payload)) {
        throw std::invalid_argument("Engine input must be a JSON object.");
      }
      if (payload.size() > kMaxJsonInputBytes) {
        throw std::invalid_argument("engine input is too large");
      }
      const auto parse_started = SteadyClock::now();
      const EngineInput input = parse_engine_input(payload);
      const double input_parse_ms = std::chrono::duration<double, std::milli>(
          SteadyClock::now() - parse_started).count();
      control.Checkpoint();

      // The core checks this control during computational work. JSON parsing
      // and serialization still only check at their surrounding boundaries.
      EngineResult result = compute_reachability(input.graph, input.request, control);
      control.Checkpoint();
      result.stage_timings_ms.emplace_back("inputParse", input_parse_ms);
      json = serialize_engine_result(result);
      control.Checkpoint();
    } catch (const ExecutionInterrupted&) {
      // Cancellation is an RPC status, not a JSON ENGINE_ERROR business result.
      throw;
    } catch (const OriginNotOnWalkway& error) {
      business_code = "ORIGIN_NOT_ON_WALKWAY";
      json = error_json(business_code, error.what());
    } catch (const AmbiguousOriginSide& error) {
      business_code = "AMBIGUOUS_ORIGIN_SIDE";
      json = error_json(business_code, error.what());
    } catch (const std::out_of_range& error) {
      business_code = "INVALID_INPUT";
      json = error_json(business_code, error.what());
    } catch (const std::invalid_argument& error) {
      business_code = "INVALID_INPUT";
      json = error_json(business_code, error.what());
    } catch (const std::exception&) {
      business_code = "ENGINE_ERROR";
      json = error_json(business_code, "Calculation failed.");
    }

    // The guard survives stack unwinding and response construction. Detecting
    // a stop never frees the slot before the owned application work is gone.
    return log.Finish(SetReply(*context, *response, std::move(json)),
                      business_code);
  } catch (const ExecutionInterrupted& stopped) {
    response->clear_response_json();
    return log.Finish(StatusForStop(stopped.reason()));
  } catch (...) {
    response->clear_response_json();
    return log.Finish({::grpc::StatusCode::INTERNAL, "RPC failed."},
                      "ENGINE_ERROR");
  }
}

::grpc::Status EngineServiceImpl::Health(
    ::grpc::ServerContext* context,
    const ::livingcircle::engine::v1::HealthRequest*, JsonReply* response) {
  CallLog log("Health");
  try {
    response->clear_response_json();
    if (auto status = AdmissionStatus(*context, draining_)) {
      return log.Finish(std::move(*status));
    }
    // Health has no compute permit and remains independent of busy CPU work.
    auto status = SetReply(*context, *response, health_json());
    if (status.ok()) {
      if (auto admission = AdmissionStatus(*context, draining_)) {
        response->clear_response_json();
        return log.Finish(std::move(*admission));
      }
    }
    return log.Finish(std::move(status));
  } catch (...) {
    response->clear_response_json();
    return log.Finish({::grpc::StatusCode::INTERNAL, "RPC failed."},
                      "ENGINE_ERROR");
  }
}

}  // namespace isochrone::rpc
