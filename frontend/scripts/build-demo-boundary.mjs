// Rebuild the road-bounded demonstration area from OpenStreetMap way geometry.
// Usage: node frontend/scripts/build-demo-boundary.mjs
// The script writes GeoJSON to stdout; review it before committing an asset.

const corners = {
  northwest: [121.5021727, 31.3450817],
  northeast: [121.5184178, 31.3422673],
  southeast: [121.5070263, 31.3218871],
  southwest: [121.491174, 31.3225124],
};

const legs = [
  { road: "国帆路", from: "northwest", to: "northeast" },
  { road: "江湾城路", from: "northeast", to: "southeast" },
  { road: "殷高东路", from: "southeast", to: "southwest" },
  { road: "国权北路", from: "southwest", to: "northwest" },
];

const query = `[out:json][timeout:30];way[highway][name~"^(国帆路|国权北路|殷高东路|江湾城路)$"](31.318,121.489,31.349,121.520);out geom;`;
const endpoint = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

function pointKey([lng, lat]) {
  return `${lng.toFixed(7)},${lat.toFixed(7)}`;
}

function distanceMeters(a, b) {
  const radians = Math.PI / 180;
  const meanLatitude = (a[1] + b[1]) / 2 * radians;
  return Math.hypot(
    (a[0] - b[0]) * 111320 * Math.cos(meanLatitude),
    (a[1] - b[1]) * 111320,
  );
}

function shortestRoadPath(ways, road, start, end) {
  const graph = new Map();
  const points = new Map();
  for (const way of ways.filter((item) => item.tags?.name === road)) {
    for (let index = 1; index < way.geometry.length; index += 1) {
      const a = [way.geometry[index - 1].lon, way.geometry[index - 1].lat];
      const b = [way.geometry[index].lon, way.geometry[index].lat];
      const aKey = pointKey(a);
      const bKey = pointKey(b);
      points.set(aKey, a);
      points.set(bKey, b);
      if (!graph.has(aKey)) graph.set(aKey, []);
      if (!graph.has(bKey)) graph.set(bKey, []);
      const length = distanceMeters(a, b);
      graph.get(aKey).push([bKey, length]);
      graph.get(bKey).push([aKey, length]);
    }
  }

  // Divided roads can use different carriageways at the two junctions. Join
  // each exact corner to the closest continuous named-road path within 30 m.
  const maxJunctionOffsetMeters = 30;
  const startCandidates = [...points].map(([key, point]) => ({
    key, offset: distanceMeters(start, point),
  })).filter((item) => item.offset <= maxJunctionOffsetMeters);
  const endCandidates = new Map([...points].map(([key, point]) => [
    key, distanceMeters(end, point),
  ]).filter(([, offset]) => offset <= maxJunctionOffsetMeters));
  if (!startCandidates.length || !endCandidates.size) {
    throw new Error(`${road}: junction was not found near road geometry`);
  }

  const distances = new Map(startCandidates.map(({ key, offset }) => [key, offset]));
  const previous = new Map();
  const settled = new Set();
  let target;
  let targetTotal = Infinity;
  while (true) {
    let current;
    let best = Infinity;
    for (const [key, value] of distances) {
      if (!settled.has(key) && value < best) {
        current = key;
        best = value;
      }
    }
    if (!current || best >= targetTotal) break;
    settled.add(current);
    if (endCandidates.has(current) && best + endCandidates.get(current) < targetTotal) {
      target = current;
      targetTotal = best + endCandidates.get(current);
    }
    for (const [next, length] of graph.get(current)) {
      const candidate = best + length;
      if (candidate < (distances.get(next) ?? Infinity)) {
        distances.set(next, candidate);
        previous.set(next, current);
      }
    }
  }
  if (!target) throw new Error(`${road}: junctions are not connected`);

  const path = [];
  for (let current = target; current; current = previous.get(current)) {
    path.push(points.get(current));
  }
  path.reverse();
  const result = [start, ...path, end].filter((point, index, all) =>
    index === 0 || pointKey(point) !== pointKey(all[index - 1]));
  console.error(`${road}: ${result.length} vertices, ${Math.round(targetTotal)} m, junction offsets ${Math.round(distanceMeters(start, path[0]))}/${Math.round(distanceMeters(end, path.at(-1)))} m`);
  return result;
}

const response = await fetch(endpoint, {
  headers: {
    "User-Agent": "15min-LivingCircle boundary builder https://github.com/Sa4pphire/15min-LivingCircle",
  },
  signal: AbortSignal.timeout(45000),
});
if (!response.ok) throw new Error(`OpenStreetMap Overpass returned ${response.status}`);
const data = await response.json();
const ring = [];
for (const leg of legs) {
  const path = shortestRoadPath(data.elements, leg.road, corners[leg.from], corners[leg.to]);
  ring.push(...(ring.length ? path.slice(1) : path));
}
if (pointKey(ring[0]) !== pointKey(ring.at(-1))) {
  throw new Error("Boundary is not a closed ring");
}

const result = {
  type: "Feature",
  geometry: { type: "Polygon", coordinates: [ring] },
  properties: {
    name: "四路围合演示区",
    coordType: "wgs84ll",
    boundaryBasis: "road-centreline",
    source: "OpenStreetMap contributors",
    sourceLicense: "ODbL 1.0",
    sourceUrl: "https://www.openstreetmap.org/copyright",
    sourceQuery: query,
    cornerOrder: ["northwest", "northeast", "southeast", "southwest"],
    roads: legs.map((leg) => leg.road),
  },
};
console.log(JSON.stringify(result, null, 2));
