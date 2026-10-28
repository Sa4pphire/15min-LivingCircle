#pragma once

#include <cstddef>
#include <utility>
#include <vector>

#include "computation_graph.hpp"

namespace isochrone::detail {

std::vector<double> shortest_times(
    const std::vector<std::vector<Arc>>& adjacency,
    const std::vector<std::pair<std::size_t, double>>& sources,
    const ExecutionControl& control,
    std::vector<std::size_t>* previous_nodes = nullptr,
    std::vector<std::size_t>* previous_edges = nullptr);

}  // namespace isochrone::detail
