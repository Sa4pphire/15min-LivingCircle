#pragma once

#include <vector>

#include "isochrone/analysis_result.hpp"
#include "computation_graph.hpp"

namespace isochrone::detail {

void append_coverage_results(
    const ComputationGraph& state, const AnalysisRequest& request,
    const std::vector<double>& arrival, double reachable_street_length,
    EngineResult& result, const ExecutionControl& control);

}  // namespace isochrone::detail
