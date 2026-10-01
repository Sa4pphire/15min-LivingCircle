// Shared source metadata, not a proximity-based divided-road detector.
// The preview keeps two carriageway lines; the engine adapter uses this same
// explicit section list to omit their inward-facing sidewalk spans.
export function attachDividedRoadSections(graph, specification) {
  if (specification?.schemaVersion !== 1 || specification.coordinateSystem !== graph.coordinateSystem ||
      !Array.isArray(specification.sections)) throw new Error("INVALID_DIVIDED_ROAD_SPECIFICATION");
  const byId = new Map(graph.edges.map(edge => [edge.id, edge]));
  const seen = new Set();
  for (const section of specification.sections) {
    const bounds = section.boundsMeters;
    if (typeof section.id !== "string" || !section.id || seen.has(section.id) ||
        section.medianWalkable !== false ||
        !["user_marked_unverified", "geometry_inferred_unverified"].includes(section.verificationStatus) ||
        typeof section.source !== "string" || !section.source ||
        !bounds || ![bounds.minX, bounds.maxX, bounds.minY, bounds.maxY].every(Number.isFinite) ||
        bounds.minX >= bounds.maxX || bounds.minY >= bounds.maxY ||
        !Array.isArray(section.carriageways) || section.carriageways.length !== 2 ||
        new Set(section.carriageways.map(c => c.edgeId)).size !== 2 ||
        section.carriageways.some(c => byId.get(c.edgeId)?.kind !== "roadMajor" ||
          !["left", "right"].includes(c.outerSide)) ||
        (section.junctionApproachClosures !== undefined &&
          (!Array.isArray(section.junctionApproachClosures) ||
            section.junctionApproachClosures.some(c => !c || typeof c.junctionId !== "string" ||
              !c.junctionId || !Array.isArray(c.portIds) || c.portIds.length !== 2 ||
              new Set(c.portIds).size !== 2 || c.portIds.some(id => typeof id !== "string" || !id) ||
              !Array.isArray(c.connectorIds) || c.connectorIds.length !== 2 ||
              new Set(c.connectorIds).size !== 2 ||
              c.connectorIds.some(id => typeof id !== "string" || !id)))))
      throw new Error("INVALID_DIVIDED_ROAD_SECTION");
    seen.add(section.id);
  }
  return { ...graph, dividedRoadSections: structuredClone(specification.sections) };
}
