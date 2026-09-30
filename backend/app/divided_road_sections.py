"""Explicit local exterior-sidewalk masks; never infer road pairing globally.

The source selection bounds are preview-local (south-positive Y). The v2
network is north-positive. Cut only the two named inward sidewalks and retain
their outside portions. An explicitly listed junction closure may retire an
inward port and its median crossing; every other connected node is rejected.
"""

from copy import deepcopy
import math
from typing import Any


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def clip_interval(a: list[float], b: list[float], bounds: dict) -> tuple[float, float] | None:
    """Liang-Barsky interval inside an axis-aligned, engine-coordinate box."""
    low, high = 0.0, 1.0
    for i, start, end in ((0, bounds['minX'], bounds['maxX']), (1, bounds['minY'], bounds['maxY'])):
        delta = b[i] - a[i]
        if abs(delta) < 1e-12:
            if a[i] < start or a[i] > end:
                return None
            continue
        first, last = sorted(((start - a[i]) / delta, (end - a[i]) / delta))
        low, high = max(low, first), min(high, last)
        if high <= low:
            return None
    return low, high


def apply_divided_road_sections(
    source: dict, nodes: dict[str, dict], edges: list[dict], junction_records: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    sections = source.get('dividedRoadSections', [])
    if not isinstance(sections, list):
        raise ValueError('divided road sections must be a list')
    if not sections:
        return edges, junction_records, []
    raw_nodes = {n['id']: n for n in source['nodes']}
    raw_edges = {e['id']: e for e in source['edges']}
    seen: set[str] = set()
    result = list(edges)
    records = deepcopy(junction_records)
    reports = []
    for section in sections:
        label = section.get('id') if isinstance(section, dict) else None
        if not isinstance(label, str) or not label or label in seen:
            raise ValueError('divided road section needs a unique ID')
        seen.add(label)
        if (section.get('medianWalkable') is not False or
                section.get('verificationStatus') != 'user_marked_unverified' or
                not isinstance(section.get('source'), str) or not section['source']):
            raise ValueError('divided road section needs explicit unverified no-median annotation')
        box = section.get('boundsMeters')
        if (not isinstance(box, dict) or not all(_finite(box.get(k)) for k in ('minX', 'maxX', 'minY', 'maxY')) or
                box['minX'] >= box['maxX'] or box['minY'] >= box['maxY']):
            raise ValueError('divided road section has invalid preview bounds')
        bounds = {**box, 'minY': -box['maxY'], 'maxY': -box['minY']}
        pair = section.get('carriageways')
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(not isinstance(c, dict) or c.get('outerSide') not in ('left', 'right') or
                    c.get('edgeId') not in raw_edges or raw_edges[c['edgeId']]['kind'] != 'roadMajor' for c in pair) or
                len({c['edgeId'] for c in pair}) != 2):
            raise ValueError('divided road section must explicitly select two major carriageways')
        inner_ids = {f"{c['edgeId']}:{'left' if c['outerSide'] == 'right' else 'right'}"
                     for c in pair}
        closures = section.get('junctionApproachClosures', [])
        if not isinstance(closures, list):
            raise ValueError('junction approach closures must be a list')
        retired_links: set[str] = set()
        closed_ports: set[str] = set()
        closure_reports: list[dict] = []
        for closure in closures:
            if not isinstance(closure, dict):
                raise ValueError('invalid divided-road junction closure')
            junction_id = closure.get('junctionId')
            port_ids = closure.get('portIds')
            connector_ids = closure.get('connectorIds')
            if (not isinstance(junction_id, str) or not junction_id or
                    not isinstance(port_ids, list) or len(port_ids) != 2 or
                    not all(isinstance(p, str) and p for p in port_ids) or
                    len(set(port_ids)) != 2 or
                    not isinstance(connector_ids, list) or len(connector_ids) != 2 or
                    not all(isinstance(c, str) and c for c in connector_ids) or
                    len(set(connector_ids)) != 2):
                raise ValueError('closure must name two inward ports and two median crossings')
            matches = [r for r in records if r['id'] == junction_id]
            if len(matches) != 1:
                raise ValueError('closure refers to an unknown junction')
            record = matches[0]
            ports = {p['id']: p for p in record['ports']}
            if (not set(port_ids) <= ports.keys() or
                    {ports[p]['edgeId'] for p in port_ids} != inner_ids or
                    any(ports[p]['nodeId'] in closed_ports for p in port_ids)):
                raise ValueError('closure must select the paired inward sidewalk ports')
            connectors = {c['id']: c for c in record['connectors']}
            incident = {c['id'] for c in record['connectors']
                        if c['fromPort'] in port_ids or c['toPort'] in port_ids}
            if (incident != set(connector_ids) or not incident <= connectors.keys() or
                    retired_links & {connectors[c]['edgeId'] for c in incident} or
                    not {connectors[c]['edgeId'] for c in incident} <= {e['id'] for e in result}):
                raise ValueError('closure must account for every inward-port connection')
            terminal = []
            for connector_id in connector_ids:
                connector = connectors[connector_id]
                if connector['kind'] != 'crossing' or not connector_id.startswith('median-'):
                    raise ValueError('only explicit median crossings can be closed')
                other = (connector['toPort'] if connector['fromPort'] in port_ids
                         else connector['fromPort'])
                if other not in ports or other in port_ids:
                    raise ValueError('closed median crossing needs a retained opposite port')
                terminal.append(other)
                retired_links.add(connector['edgeId'])
            if len(set(terminal)) != 2:
                raise ValueError('closed median crossings must end at distinct ports')
            for port_id in port_ids:
                port = ports[port_id]
                node_id = port['nodeId']
                if any(e['id'] != port['edgeId'] and e['id'] not in retired_links and
                       node_id in (e['from'], e['to']) for e in result):
                    raise ValueError('closure touches an unlisted junction or access connection')
                # The selected port itself must lie inside the local mask.
                point = [nodes[node_id]['xMeters'], nodes[node_id]['yMeters']]
                if not (bounds['minX'] <= point[0] <= bounds['maxX'] and
                        bounds['minY'] <= point[1] <= bounds['maxY']):
                    raise ValueError('closed port lies outside divided-road mask')
                closed_ports.add(node_id)
            record['ports'] = [p for p in record['ports'] if p['id'] not in port_ids]
            record['connectors'] = [c for c in record['connectors'] if c['id'] not in connector_ids]
            record.setdefault('terminalMedianPortIds', []).extend(terminal)
            closure_reports.append({'junctionId': junction_id, 'portIds': port_ids,
                                    'connectorIds': connector_ids, 'terminalMedianPortIds': terminal})
        result = [e for e in result if e['id'] not in retired_links]
        centers = []
        for carriageway in pair:
            raw = raw_edges[carriageway['edgeId']]
            points = [[raw_nodes[n]['x'], -raw_nodes[n]['y']] for n in (raw['from'], raw['to'])]
            interval = clip_interval(*points, bounds)
            if interval is None:
                raise ValueError('selected carriageway does not traverse the local section')
            t = sum(interval) / 2
            centers.append([points[0][i] + t * (points[1][i] - points[0][i]) for i in (0, 1)])
        removed = []
        replacements: dict[str, list[dict]] = {}
        for index, carriageway in enumerate(pair):
            inner_side = 'left' if carriageway['outerSide'] == 'right' else 'right'
            inner_id = f"{carriageway['edgeId']}:{inner_side}"
            outer_id = f"{carriageway['edgeId']}:{carriageway['outerSide']}"
            inner = [e for e in result if e['kind'] == 'sidewalk' and
                     (e['id'] == inner_id or e.get('sourceSidewalkEdgeId', e.get('originalEdgeId')) == inner_id)]
            outer = [e for e in result if e['kind'] == 'sidewalk' and
                     (e['id'] == outer_id or e.get('sourceSidewalkEdgeId', e.get('originalEdgeId')) == outer_id)]
            if not inner or not outer:
                raise ValueError('divided road section refers to missing or already masked sidewalks')
            # Verify the explicitly named side is physically away from the
            # other carriageway. OSM way directions can be opposite.
            def midpoint(candidates: list[dict]) -> list[float]:
                for candidate in candidates:
                    interval = clip_interval(*candidate['pathMeters'], bounds)
                    if interval is not None:
                        t = sum(interval) / 2
                        a, b = candidate['pathMeters']
                        return [a[i] + t * (b[i]-a[i]) for i in (0, 1)]
                raise ValueError('sidewalk does not traverse selected section')
            if math.dist(midpoint(outer), centers[1-index]) <= math.dist(midpoint(inner), centers[1-index]) + 0.1:
                raise ValueError('selected outer sidewalk actually faces the median')
            for edge in inner:
                if len(edge['pathMeters']) != 2:
                    raise ValueError('local divided-road masks require straight preview segments')
                a, b = edge['pathMeters']
                interval = clip_interval(a, b, bounds)
                if interval is None:
                    continue
                low, high = interval
                length = math.dist(a, b)
                if (high-low) * length < 0.05:
                    continue
                # Fail closed rather than drop a branch, facility entrance,
                # turn or crossing attached to an inner sidewalk.
                removed_endpoints = [node for node, t in ((edge['from'], 0.0), (edge['to'], 1.0))
                                     if low-1e-9 <= t <= high+1e-9]
                if any(e['id'] != edge['id'] and (e['from'] in removed_endpoints or e['to'] in removed_endpoints)
                       for e in result):
                    raise ValueError('local mask touches a connected node; explicit junction/access redesign required')
                pieces = []
                for part, start, end in (('before', 0.0, low), ('after', high, 1.0)):
                    if (end-start) * length < 0.05:
                        continue
                    point_a = [round(a[i]+start*(b[i]-a[i]), 3) for i in (0, 1)]
                    point_b = [round(a[i]+end*(b[i]-a[i]), 3) for i in (0, 1)]
                    from_id = edge['from'] if start == 0 else f"divided:{label}:{edge['id']}:after"
                    to_id = edge['to'] if end == 1 else f"divided:{label}:{edge['id']}:before"
                    for node_id, point in ((from_id, point_a), (to_id, point_b)):
                        if node_id not in nodes:
                            nodes[node_id] = {'id': node_id, 'xMeters': point[0], 'yMeters': point[1]}
                    pieces.append({**edge, 'id': f"{edge['id']}:divided:{label}:{part}",
                                   'from': from_id, 'to': to_id, 'pathMeters': [point_a, point_b],
                                   'sourceSidewalkEdgeId': inner_id, 'dividedRoadSectionId': label})
                replacements[edge['id']] = pieces
                removed.append({'edgeId': edge['id'], 'sourceSidewalkEdgeId': inner_id,
                                'removedLengthMeters': round((high-low)*length, 3),
                                'replacementEdgeIds': [p['id'] for p in pieces]})
        if len(removed) < 2:
            raise ValueError('local section must mask the inward sidewalk of both carriageways')
        result = [piece for edge in result for piece in replacements.get(edge['id'], [edge])]
        for record in records:
            for port in record['ports']:
                if port['edgeId'] not in replacements:
                    continue
                candidates = [e for e in replacements[port['edgeId']]
                              if port['nodeId'] in (e['from'], e['to'])]
                if len(candidates) != 1:
                    raise ValueError('local mask removes an explicitly reviewed junction port')
                port['sourceApproachEdgeId'] = port['edgeId']
                port['edgeId'] = candidates[0]['id']
        reports.append({**section, 'coordinateSystem': 'preview-local-v1', 'maskedSidewalks': removed,
                        'appliedJunctionClosures': closure_reports,
                        'retainedOuterSidewalkIds': [f"{c['edgeId']}:{c['outerSide']}" for c in pair]})
    return result, records, reports
