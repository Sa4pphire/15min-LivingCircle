#pragma once

#include <atomic>
#include <memory>

#include <grpcpp/server_context.h>
#include <grpcpp/support/status.h>

#include "compute_gate.hpp"
#include "engine_service.grpc.pb.h"
#include "fixed_graph_store.hpp"
#include "rpc_options.hpp"

namespace isochrone::rpc {

class EngineServiceImpl final
    : public ::livingcircle::engine::v1::EngineService::Service {
 public:
  explicit EngineServiceImpl(
      int compute_capacity = kDefaultComputeCapacity,
      std::unique_ptr<const GraphSnapshot> snapshot = {});

  void BeginDrain() noexcept;

  ::grpc::Status Compute(
      ::grpc::ServerContext* context,
      const ::livingcircle::engine::v1::ComputeRequest* request,
      ::livingcircle::engine::v1::JsonReply* response) override;

  ::grpc::Status ComputeOnGraph(
      ::grpc::ServerContext* context,
      const ::livingcircle::engine::v1::GraphComputeRequest* request,
      ::livingcircle::engine::v1::GraphComputeReply* response) override;

  ::grpc::Status Health(
      ::grpc::ServerContext* context,
      const ::livingcircle::engine::v1::HealthRequest* request,
      ::livingcircle::engine::v1::JsonReply* response) override;

 private:
  FixedGraphStore graphs_;
  std::atomic<bool> draining_{false};
  ComputeGate computing_;
};

}  // namespace isochrone::rpc
