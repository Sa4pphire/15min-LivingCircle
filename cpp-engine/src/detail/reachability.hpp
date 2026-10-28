#pragma once

#include <vector>

#include "isochrone/analysis_result.hpp"
#include "computation_graph.hpp"
#include "display_geometry.hpp"

namespace isochrone::detail {

std::vector<Interval> reachable_intervals(
    const InternalEdge& edge, const std::vector<double>& times,
    double threshold, double speed);
std::vector<Interval> subtract_intervals(
    const std::vector<Interval>& base, const std::vector<Interval>& covered,
    const ExecutionControl& control);
std::vector<Interval> intersect_intervals(
    const std::vector<Interval>& first, const std::vector<Interval>& second,
    const ExecutionControl& control);

// timed_edges borrow state.edges; the caller retains the completed state
// until synchronous display generation has finished.
struct ReachabilitySummary {
  double reachable_street_length{};
  bool blocked_crossing{};
  std::vector<TimedDisplayEdge> timed_edges;
};
ReachabilitySummary append_reachable_edges(
    const ComputationGraph& state, const AnalysisRequest& request,
    const std::vector<double>& arrival, EngineResult& result,
    const ExecutionControl& control);

}  // namespace isochrone::detail
