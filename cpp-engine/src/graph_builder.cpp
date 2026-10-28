#include "detail/computation_graph.hpp"

#include <algorithm>
#include <cmath>
#include <unordered_map>

#include "detail/path_geometry.hpp"

namespace isochrone::detail {

ComputationGraph build_computation_graph(
    const RoadGraph& graph, const AnalysisRequest& request, const Snap& snap,
    const ExecutionControl& control) {
  ComputationGraph state;
  auto& node_indices = state.node_indices;
  auto& graph_points = state.graph_points;
  for (std::size_t i = 0; i < graph.nodes.size(); ++i) {
    control.Poll();
    node_indices.emplace(graph.nodes[i].id, i);
    graph_points.push_back(graph.nodes[i].point);
  }
  std::unordered_map<std::string, std::size_t> edge_indices;
  struct Mark { double offset; std::size_t node; };
  control.Checkpoint();
  std::vector<std::vector<Mark>> marks(graph.edges.size());
  control.Checkpoint();
  for (std::size_t i = 0; i < graph.edges.size(); ++i) {
    control.Poll();
    const WalkEdge& edge = graph.edges[i];
    edge_indices.emplace(edge.id, i);
    marks[i].push_back({0.0, node_indices.at(edge.from)});
    marks[i].push_back({path_length(edge.path, control), node_indices.at(edge.to)});
  }
  const auto register_position = [&](std::size_t edge_index, double offset) {
    for (const Mark mark : marks[edge_index]) {
      control.Poll();
      if (std::abs(mark.offset - offset) <= kEpsilon) return mark.node;
    }
    const std::size_t node = graph_points.size();
    graph_points.push_back(point_at(graph.edges[edge_index].path, offset, control));
    marks[edge_index].push_back({offset, node});
    return node;
  };
  state.origin_index =
      register_position(snap.edge_index, snap.offset_meters);

  auto& bindings = state.bindings;
  for (std::size_t facility_index = 0;
       facility_index < request.facilities.size(); ++facility_index) {
    control.Poll();
    const FacilityAccess& facility = request.facilities[facility_index];
    const auto add_entrance = [&](const FacilityEntrance& entrance) {
      const std::size_t edge_index = edge_indices.at(entrance.access_edge_id);
      const Snap access = project_to_edge(
          entrance.street_access_point, graph.edges[edge_index], edge_index, control);
      const std::size_t node = register_position(edge_index, access.offset_meters);
      const double connector_length = access.distance_meters +
          path_length(entrance.access_path, control);
      bindings.push_back({facility_index, entrance.id, entrance.access_edge_id,
          entrance.street_access_point, !entrance.access_path.empty(), node,
          connector_length / request.walking_speed_meters_per_second, entrance.access_path});
    };
    if (facility.entrances.empty()) {
      add_entrance({facility.id, facility.access_edge_id,
                    facility.access_point, {}});
    } else {
      for (const FacilityEntrance& entrance : facility.entrances) {
        control.Poll();
        add_entrance(entrance);
      }
    }
  }

  auto& edges = state.edges;
  for (std::size_t i = 0; i < graph.edges.size(); ++i) {
    control.Poll();
    const WalkEdge& source = graph.edges[i];
    auto& points = marks[i];
    const bool check_sort = points.size() >= 256;
    if (check_sort) control.Checkpoint();
    std::sort(points.begin(), points.end(), [](Mark a, Mark b) {
      return a.offset < b.offset;
    });
    if (check_sort) control.Checkpoint();
    for (std::size_t j = 1; j < points.size(); ++j) {
      control.Poll();
      const Mark from = points[j - 1];
      const Mark to = points[j];
      if (to.offset - from.offset <= kEpsilon) continue;
      edges.push_back({source.id, source.kind, from.node, to.node,
          slice(source.path, from.offset, to.offset, control), to.offset - from.offset,
          from.offset, source.width_meters,
          source.kind == EdgeKind::crossing
              ? source.wait_seconds.value_or(request.crossing_wait_seconds)
              : 0.0});
    }
  }

  control.Checkpoint();
  state.adjacency.resize(graph_points.size());
  auto& adjacency = state.adjacency;
  control.Checkpoint();
  for (std::size_t edge_index = 0; edge_index < edges.size(); ++edge_index) {
    control.Poll();
    const auto& edge = edges[edge_index];
    const double cost = edge.length / request.walking_speed_meters_per_second +
                        edge.wait_seconds;
    adjacency[edge.from].push_back({edge.to, cost, edge_index});
    adjacency[edge.to].push_back({edge.from, cost, edge_index});
  }
  return state;
}

}  // namespace isochrone::detail
