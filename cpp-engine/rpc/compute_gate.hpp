#pragma once

#include <atomic>
#include <stdexcept>

namespace isochrone::rpc {

// Bounds active application work only. There is no waiting queue, and gRPC
// retains ownership of its reception threads and transport resources.
class ComputeGate {
 public:
  explicit ComputeGate(int capacity) : capacity_(capacity) {
    if (capacity < 1) {
      throw std::invalid_argument("Compute capacity must be positive.");
    }
  }

  ComputeGate(const ComputeGate&) = delete;
  ComputeGate& operator=(const ComputeGate&) = delete;

  [[nodiscard]] bool TryAcquire() noexcept {
    int active = active_.load(std::memory_order_relaxed);
    while (active < capacity_) {
      if (active_.compare_exchange_weak(active, active + 1,
                                       std::memory_order_acquire,
                                       std::memory_order_relaxed)) {
        return true;
      }
    }
    return false;
  }

  // The request's RAII permit calls this exactly once after successful
  // acquisition, after its owned work and cleanup have really finished.
  void Release() noexcept {
    active_.fetch_sub(1, std::memory_order_release);
  }

 private:
  const int capacity_;
  std::atomic<int> active_{0};
};

}  // namespace isochrone::rpc
