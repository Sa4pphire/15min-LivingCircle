#include "detail/shortest_path.hpp"

#include <functional>
#include <limits>
#include <queue>

#include "detail/path_geometry.hpp"

namespace isochrone::detail {

std::vector<double> shortest_times(
    const std::vector<std::vector<Arc>>& adjacency,
    const std::vector<std::pair<std::size_t, double>>& sources,
    const ExecutionControl& control,
    std::vector<std::size_t>* previous_nodes,
    std::vector<std::size_t>* previous_edges) {
  control.Checkpoint();
  if (previous_nodes) previous_nodes->assign(adjacency.size(), adjacency.size());
  if (previous_edges) previous_edges->assign(adjacency.size(), adjacency.size());
  std::vector<double> times(adjacency.size(),
                            std::numeric_limits<double>::infinity());
  control.Checkpoint();
  using Item = std::pair<double, std::size_t>;
  std::priority_queue<Item, std::vector<Item>, std::greater<Item>> queue;
  for (const auto& [node, cost] : sources) {
    control.Poll();
    if (cost < times[node]) {
      times[node] = cost;
      queue.emplace(cost, node);
    }
  }
  while (!queue.empty()) {
    control.Poll();
    const auto [time, node] = queue.top();
    queue.pop();
    if (time > times[node] + kEpsilon) continue;
    for (const Arc arc : adjacency[node]) {
      control.Poll();
      if (time + arc.cost < times[arc.to]) {
        times[arc.to] = time + arc.cost;
        if (previous_nodes) (*previous_nodes)[arc.to] = node;
        if (previous_edges) (*previous_edges)[arc.to] = arc.edge_index;
        queue.emplace(times[arc.to], arc.to);
      }
    }
  }
  return times;
}

}  // namespace isochrone::detail
