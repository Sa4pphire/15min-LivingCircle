#pragma once

#include <cstddef>
#include <vector>

#include "isochrone/analysis_result.hpp"
#include "computation_graph.hpp"

namespace isochrone::detail {

void append_facility_results(
    const RoadGraph& graph, const AnalysisRequest& request, const Snap& snap,
    double access_seconds, const ComputationGraph& state,
    const std::vector<double>& arrival,
    const std::vector<std::size_t>& previous_nodes,
    const std::vector<std::size_t>& previous_edges,
    EngineResult& result, const ExecutionControl& control);

}  // namespace isochrone::detail
