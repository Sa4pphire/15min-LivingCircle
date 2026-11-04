#pragma once

#include "isochrone/road_graph.hpp"
#include "isochrone/analysis_request.hpp"
#include "isochrone/execution_control.hpp"
#include "isochrone/graph_snapshot.hpp"

namespace isochrone::detail {

void validate_schema_version(int schema_version);
// Keep the joint parameter/node/local/edge/category/facility error order.
void validate_input(const RoadGraph& graph, const AnalysisRequest& request,
                    const ExecutionControl& control);
// Validate only immutable graph invariants and retain owned ID-to-index maps.
[[nodiscard]] GraphIndex validate_static_graph(
    const RoadGraph& graph, const ExecutionControl& control);
// The snapshot's graph was validated at construction. Request fields and their
// references/geometry must still be checked on every call.
void validate_snapshot_request(const GraphSnapshot& snapshot,
                               const AnalysisRequest& request,
                               const ExecutionControl& control);

}  // namespace isochrone::detail
