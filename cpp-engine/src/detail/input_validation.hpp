#pragma once

#include "isochrone/road_graph.hpp"
#include "isochrone/analysis_request.hpp"
#include "isochrone/execution_control.hpp"

namespace isochrone::detail {

void validate_schema_version(int schema_version);
// Keep the joint parameter/node/local/edge/category/facility error order.
void validate_input(const RoadGraph& graph, const AnalysisRequest& request,
                    const ExecutionControl& control);

}  // namespace isochrone::detail
