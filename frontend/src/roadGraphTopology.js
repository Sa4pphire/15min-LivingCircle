// Source-node evidence, not nearest-neighbour repair. Review data belongs to
// synthetic-preview.annotations.json; unchanged road edges retain their IDs.
const DENIED = new Set(["no", "private", "use_sidepath"]);
const ALLOWED = new Set(["yes", "designated", "permissive"]);

export function permitsPedestrians(tags = {}) {
  if (DENIED.has(tags.foot)) return false;
  if (DENIED.has(tags.access) && !ALLOWED.has(tags.foot)) return false;
  return tags.highway !== "cycleway" || ALLOWED.has(tags.foot);
}

function validate(review) {
  if (!review) return;
  if (review.schemaVersion !== 1 || review.coordinateSystem !== "preview-local-v1" ||
      !Array.isArray(review.ways) || !Array.isArray(review.junctions) || !review.sourceUrl) {
    throw new Error("INVALID_SOURCE_TOPOLOGY_REVIEW");
  }
  const ids = new Set();
  for (const way of review.ways) {
    if (!Number.isSafeInteger(way.id) || ids.has(way.id) || !way.tags || !Array.isArray(way.nodeIds)) {
      throw new Error("INVALID_SOURCE_WAY_EVIDENCE");
    }
    ids.add(way.id);
  }
  const nodeIds = new Set();
  for (const junction of review.junctions) {
    if (!Number.isSafeInteger(junction.sourceNodeId) || nodeIds.has(junction.sourceNodeId) ||
        !Array.isArray(junction.pointMeters) || junction.pointMeters.length !== 2 ||
        !junction.pointMeters.every(Number.isFinite) || !Array.isArray(junction.wayIds) ||
        new Set(junction.wayIds).size < 2 || junction.wayIds.some(id =>
          !review.ways.find(way => way.id === id)?.nodeIds.includes(junction.sourceNodeId))) {
      throw new Error("JUNCTION_REQUIRES_SHARED_SOURCE_NODE");
    }
    nodeIds.add(junction.sourceNodeId);
  }
}

export function prepareTopologyFeatures(features, review) {
  validate(review);
  if (!review) return features.filter(feature => permitsPedestrians(feature.sourceTags));
  const ways = new Map(review.ways.map(way => [way.id, way]));
  const combined = new Map(features.map(feature => [feature.id, feature]));
  for (const feature of review.additionalFeatures ?? []) {
    if (combined.has(feature.id) || !ways.has(feature.id) || !permitsPedestrians(ways.get(feature.id).tags)) {
      throw new Error("INVALID_RESTORED_SHORT_WAY");
    }
    combined.set(feature.id, feature);
  }
  return [...combined.values()].flatMap(feature => {
    const way = ways.get(feature.id);
    if (!way) return permitsPedestrians(feature.sourceTags) ? [feature] : [];
    if (!permitsPedestrians(way.tags)) return [];
    return [{ ...feature, sourceTags: way.tags }];
  });
}

function lockedEdges(annotations) {
  const ids = new Set();
  function visit(value) {
    if (typeof value === "string" && /^w:\d+:/.test(value)) {
      ids.add(value);
      // Annotation IDs may name the later inferred-repair :s0/:s1 child.
      // Its unsplit parent is owned too, before source-node restoration runs.
      ids.add(value.split(":").slice(0, 4).join(":"));
    }
    else if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") Object.values(value).forEach(visit);
  }
  // These records own the major-road ports and crossings; never trim them here.
  for (const key of ["crossings", "junctions", "connections", "dividedRoadSections"]) visit(annotations[key]);
  return ids;
}

function project(point, a, b) {
  const dx = b.x-a.x, dy = b.y-a.y, length2 = dx*dx+dy*dy;
  const t = length2 ? Math.max(0, Math.min(1, ((point[0]-a.x)*dx+(point[1]-a.y)*dy)/length2)) : 0;
  return { t, distance: Math.hypot(point[0]-a.x-t*dx, point[1]-a.y-t*dy) };
}

function statistics(nodes, edges, previous) {
  const adjacency = new Map(nodes.map(node => [node.id, []]));
  const edgeKinds = {};
  for (const edge of edges) {
    adjacency.get(edge.from).push(edge.to); adjacency.get(edge.to).push(edge.from);
    edgeKinds[edge.kind] = (edgeKinds[edge.kind] ?? 0)+1;
  }
  const visited = new Set(); let count=0, largest=0;
  for (const node of nodes) {
    if (visited.has(node.id)) continue;
    const queue=[node.id]; visited.add(node.id); count++;
    for (let i=0; i<queue.length; i++) for (const other of adjacency.get(queue[i])) {
      if (!visited.has(other)) { visited.add(other); queue.push(other); }
    }
    largest=Math.max(largest,queue.length);
  }
  return { ...previous, componentCount: count, largestComponentNodes: largest, edgeKinds };
}

export function restoreSourceTopology(graph, review, annotations = {}) {
  validate(review);
  if (!review) return graph;
  const nodes = new Map(graph.nodes.map(node => [node.id,{...node}]));
  const byWay = new Map(), splits = new Map(), records=[];
  const locked = lockedEdges(annotations);
  for (const edge of graph.edges) {
    if (!byWay.has(edge.sourceWayId)) byWay.set(edge.sourceWayId,[]);
    byWay.get(edge.sourceWayId).push(edge);
  }
  for (const junction of [...review.junctions].sort((a,b) => a.sourceNodeId-b.sourceNodeId)) {
    if (junction.nodeTags && (DENIED.has(junction.nodeTags.foot) ||
        DENIED.has(junction.nodeTags.access) && !ALLOWED.has(junction.nodeTags.foot) ||
        junction.nodeTags.barrier && !ALLOWED.has(junction.nodeTags.foot))) {
      records.push({...junction,status:"pending",reason:"source_node_access_review_required"});
      continue;
    }
    const point=junction.pointMeters.map(value => Number(value.toFixed(1)));
    const anchorId=`p:${point[0].toFixed(1)}:${point[1].toFixed(1)}`;
    const attachments=[]; let reason=null;
    for (const wayId of junction.wayIds) {
      const candidates=(byWay.get(wayId) ?? []).map(edge => ({ edge,
        ...project(point,nodes.get(edge.from),nodes.get(edge.to)) }))
        .sort((a,b) => a.distance-b.distance || a.edge.id.localeCompare(b.edge.id));
      const best=candidates[0];
      if (!best || best.distance>2.1) { reason="outside_preserved_geometry"; break; }
      const existing=best.edge.from===anchorId || best.edge.to===anchorId;
      // A source-node-to-road-centre attachment still needs an explicit
      // sidewalk-port/crossing design, even if OSM shares the centre vertex.
      if (best.edge.kind==="roadMajor") { reason="major_road_port_review_required"; break; }
      if (!existing && locked.has(best.edge.id)) { reason="manual_annotation_owns_edge"; break; }
      if (!existing && (best.t<=1e-8 || best.t>=1-1e-8)) { reason="endpoint_geometry_mismatch"; break; }
      attachments.push({ ...best, existing });
    }
    if (reason) { records.push({...junction,status:"pending",reason}); continue; }
    nodes.set(anchorId,{...(nodes.get(anchorId) ?? {id:anchorId,x:point[0],y:point[1]}),
                           sourceNodeId:junction.sourceNodeId});
    let restored=false;
    for (const attachment of attachments) {
      if (attachment.existing) continue;
      if (!splits.has(attachment.edge.id)) splits.set(attachment.edge.id,[]);
      splits.get(attachment.edge.id).push({t:attachment.t,nodeId:anchorId,sourceNodeId:junction.sourceNodeId});
      restored=true;
    }
    records.push({...junction,nodeId:anchorId,status:restored?"restored":"preserved"});
  }
  const edges=graph.edges.flatMap(edge => {
    const anchors=splits.get(edge.id);
    if (!anchors) return [edge];
    const ordered=[edge.from,...anchors.sort((a,b) => a.t-b.t).map(anchor => anchor.nodeId),edge.to]
      .filter((id,i,all) => !i || id!==all[i-1]);
    return ordered.slice(1).map((to,index) => {
      const from=ordered[index], a=nodes.get(from), b=nodes.get(to);
      const length=Number(Math.hypot(a.x-b.x,a.y-b.y).toFixed(1));
      if (length<0.05) throw new Error("RESTORED_SOURCE_NODE_COLLAPSED_EDGE");
      return {...edge,id:`${edge.id}:osm:${index}`,originalEdgeId:edge.id,from,to,length};
    });
  });
  const nodeList=[...nodes.values()].sort((a,b) => a.id.localeCompare(b.id));
  const junctions=new Map(graph.junctions.map(junction => [junction.nodeId,junction]));
  for (const record of records.filter(record => record.status!=="pending")) {
    junctions.set(record.nodeId,{nodeId:record.nodeId,sourceWayIds:[...record.wayIds].sort((a,b) => a-b),
                               sourceNodeId:record.sourceNodeId});
  }
  return {...graph,nodes:nodeList,edges,junctions:[...junctions.values()].sort((a,b) => a.nodeId.localeCompare(b.nodeId)),
    diagnostics:statistics(nodeList,edges,graph.diagnostics),
    sourceTopology:{sourceUrl:review.sourceUrl,verificationStatus:"online_source_unverified_on_site",
      excludedWays:review.ways.filter(way => !permitsPedestrians(way.tags)).map(way => ({id:way.id,tags:way.tags})),
      restoredShortWayIds:(review.additionalFeatures ?? []).map(feature => feature.id),junctions:records}};
}
