#pragma once

#include "isochrone/analysis_request.hpp"
#include "isochrone/analysis_result.hpp"
#include "isochrone/engine_errors.hpp"
#include "isochrone/execution_control.hpp"
#include "isochrone/road_graph.hpp"
#include "isochrone/types.hpp"

namespace isochrone {

// v2 protocol envelope. Parsing keeps the JSON flat while assigning fields
// to their owning domain objects.
struct EngineInput {
  int schema_version{2};
  RoadGraph graph;
  AnalysisRequest request;
};

// Compatibility entry point: validate the schema and the graph/request pair.
void validate_graph(const EngineInput& input);
// Inputs are borrowed only for this synchronous call. The caller keeps them
// alive and unchanged; all temporary graph and search state is local.
[[nodiscard]] EngineResult compute_reachability(
    const RoadGraph& graph, const AnalysisRequest& request);
// The request-local control is borrowed for this call. Cancellation is observed
// at explicit checkpoints; it does not interrupt a single library operation.
[[nodiscard]] EngineResult compute_reachability(
    const RoadGraph& graph, const AnalysisRequest& request,
    const ExecutionControl& control);
// Compatibility entry point: check the schema, then delegate without copying.
[[nodiscard]] EngineResult compute_reachability(const EngineInput& input);

}  // namespace isochrone
