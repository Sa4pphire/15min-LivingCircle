#pragma once

#include <cstddef>
#include <exception>
#include <functional>
#include <optional>
#include <utility>

namespace isochrone {

enum class StopReason { Cancelled, DeadlineExceeded };

class ExecutionInterrupted final : public std::exception {
 public:
  explicit ExecutionInterrupted(StopReason reason) noexcept : reason_(reason) {}

  [[nodiscard]] StopReason reason() const noexcept { return reason_; }

  [[nodiscard]] const char* what() const noexcept override {
    return reason_ == StopReason::DeadlineExceeded
        ? "Execution deadline exceeded."
        : "Execution was cancelled.";
  }

 private:
  StopReason reason_;
};

// A request owns its control and lends it to one synchronous calculation.
// The probe is transport-independent. Mutable polling progress is local to
// this request; do not share the control between concurrent calculations.
class ExecutionControl {
 public:
  using Probe = std::function<std::optional<StopReason>()>;

  ExecutionControl() = default;
  explicit ExecutionControl(Probe probe) : probe_(std::move(probe)) {}

  // Force a check at phase boundaries, including before early returns.
  void Checkpoint() const {
    if (!probe_) return;
    if (const auto reason = probe_()) throw ExecutionInterrupted(*reason);
  }

  // Count work items (for example arcs, path segments or grid cells), not
  // elapsed time. This interval is an initial choice, not a latency guarantee.
  // An empty probe bypasses the counter for existing, non-cancellable calls.
  void Poll() const {
    if (!probe_) return;
    if (++work_since_check_ == kPollInterval) {
      work_since_check_ = 0;
      Checkpoint();
    }
  }

 private:
  static constexpr std::size_t kPollInterval = 256;
  Probe probe_;
  mutable std::size_t work_since_check_{};
};

}  // namespace isochrone
