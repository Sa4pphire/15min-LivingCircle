"""Exterior-only synthetic arterial model, with explicit port migration.

The checked-in selection policy owns carriageway grouping. Never pair a nearby
road, join a geometric intersection, or silently discard an attached walkway.
"""
from collections import defaultdict
from copy import deepcopy
import math

from .crossroad_audit import group_directions


def _project(point, path):
    best=(math.inf,None,None)
    # Converter approaches are straight; preserve that invariant for splits.
    if len(path)!=2:
        raise ValueError('arterial projection requires a straight approach')
    a,b=path;d=[b[i]-a[i] for i in (0,1)];l2=sum(v*v for v in d)
    if l2<.0025:raise ValueError('arterial approach collapsed')
    t=max(0,min(1,sum((point[i]-a[i])*d[i] for i in (0,1))/l2))
    p=[a[i]+t*d[i] for i in (0,1)]
    return math.dist(point,p),p,t


def _arms(record, edges, nodes, *, physical=False):
    entries=[]
    for p in record['ports']:
        e=edges[p['edgeId']];other=e['to'] if e['from']==p['nodeId'] else e['from']
        x,y=nodes[p['nodeId']],nodes[other]
        entries.append({'port':p,'angle':math.degrees(math.atan2(y['yMeters']-x['yMeters'],y['xMeters']-x['xMeters']))%360})
    def angle(group):
        return math.atan2(sum(math.sin(math.radians(p['angle'])) for p in group),
                          sum(math.cos(math.radians(p['angle'])) for p in group))
    if physical:
        membership=defaultdict(list)
        for p in entries:membership[p['port']['approachId']].append(p)
        groups=sorted(membership.values(),key=angle)
    else:groups=sorted(group_directions(entries),key=angle)
    for group in groups:
        r=angle(group);ray=[math.cos(r),math.sin(r)]
        center=record['centerMeters']
        group.sort(key=lambda p: ray[0]*(nodes[p['port']['nodeId']]['yMeters']-center[1])-
                                ray[1]*(nodes[p['port']['nodeId']]['xMeters']-center[0]))
        for p in group:p['ray']=ray
    return groups


def apply_major_sidewalk_policy(source,nodes,edges,junction_records):
    policy=source.get('majorSidewalkPolicy')
    if not policy:return edges,junction_records,None
    if (policy.get('schemaVersion')!=1 or policy.get('mode')!='exterior_only' or
            policy.get('verificationStatus')!='user_requested_synthetic_simplification' or
            not policy.get('source') or not isinstance(policy.get('selections'),list)):
        raise ValueError('invalid exterior-sidewalk policy')
    raw={e['id']:e for e in source['edges'] if e['kind']=='roadMajor'}
    specs={r['sourceEdgeId']:r for r in policy['selections']}
    if len(specs)!=len(policy['selections']) or set(specs)!=set(raw):
        raise ValueError('exterior policy must own every major source edge exactly once')
    for key,s in specs.items():
        if (s.get('sourceWayId')!=raw[key]['sourceWayId'] or s.get('sides') not in
                (['left'],['right'],['left','right'])):
            raise ValueError('invalid major sidewalk ownership: '+key)
    byid={e['id']:e for e in edges}
    def owner(e):
        if e['kind']!='sidewalk':return None
        key=e.get('sourceMajorEdgeId')
        if key not in specs:raise ValueError('missing major source ownership: '+e['id'])
        return specs[key]
    retained={e['id']:deepcopy(e) for e in edges if not owner(e) or e['side'] in owner(e)['sides']}
    removed={e['id']:e for e in edges if e['id'] not in retained}
    outer=[e for e in retained.values() if e['kind']=='sidewalk']
    node_point=lambda id:[nodes[id]['xMeters'],nodes[id]['yMeters']]
    raw_nodes={n['id']:[n['x'],-n['y']] for n in source['nodes']}
    def opposite_carriageway(old,target,point):
        a=raw[old['sourceMajorEdgeId']];b=raw[target['sourceMajorEdgeId']]
        x,y=raw_nodes[a['from']],raw_nodes[a['to']]
        u,v=raw_nodes[b['from']],raw_nodes[b['to']]
        d=[y[i]-x[i] for i in (0,1)];f=[v[i]-u[i] for i in (0,1)]
        dot=sum(d[i]*f[i] for i in (0,1))/(math.hypot(*d)*math.hypot(*f))
        # All retained data here uses OSM way direction. Account explicitly
        # for a reversed oneway tag if a future policy includes one.
        da=-1 if a.get('sourceTags',{}).get('oneway')=='-1' else 1
        db=-1 if b.get('sourceTags',{}).get('oneway')=='-1' else 1
        if dot*da*db>=-.1:return False
        side=(d[0]*(point[1]-x[1])-d[1]*(point[0]-x[0]))/math.hypot(*d)
        return side>.5 if specs[a['id']]['sides']==['right'] else side<-.5
    by_way=defaultdict(list)
    for e in outer:by_way[raw[e['sourceMajorEdgeId']]['sourceWayId']].append(e)
    records=deepcopy(junction_records)
    # Two traffic-direction junctions can be halves of one physical crossing.
    # Coalesce only reviewed records with the same explicitly recorded street
    # ownership/level (or same crossing way), within one carriageway-width area.
    # Nothing in the unreviewed graph is connected by this grouping.
    def road_keys(record):
        keys=set()
        for p in record['ports']:
            edge=byid[p['edgeId']]
            if edge['kind']=='sidewalk':r=raw[edge['sourceMajorEdgeId']]
            else:r=next((r for r in source['edges'] if r['id']==edge['id']),None)
            if not r:continue
            tags=r.get('sourceTags',{})
            level=(tags.get('layer','0'),tags.get('bridge','no'),tags.get('tunnel','no'))
            keys.add((tags.get('name') or 'way:'+str(r['sourceWayId']),*level))
        return frozenset(keys)
    parent=list(range(len(records)));keys=[road_keys(r) for r in records]
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    for i,r in enumerate(records):
        for k in range(i):
            if len(keys[i])>=2 and keys[i]==keys[k] and math.dist(r['centerMeters'],records[k]['centerMeters'])<=60:
                parent[root(i)]=root(k)
    clusters=defaultdict(list)
    for i,r in enumerate(records):clusters[root(i)].append(r)
    merged=[];merges=[]
    for rs in clusters.values():
        if len(rs)==1:merged.append(rs[0]);continue
        r=rs[0];ids=[q['id'] for q in rs]
        r['ports']=[{**p,'id':q['id']+'/'+p['id'],'previousPortId':p['id'],
                     'previousJunctionId':q['id']} for q in rs for p in q['ports']]
        center=[sum(q['centerMeters'][i] for q in rs)/len(rs) for i in (0,1)]
        # Retire internal approach stubs joining the two already-reviewed halves.
        port_nodes={p['nodeId'] for p in r['ports']}
        internal={p['edgeId'] for p in r['ports'] if byid[p['edgeId']]['from'] in port_nodes and
                  byid[p['edgeId']]['to'] in port_nodes}
        # Include degree-two merges, not just direct port-to-port edges. This is
        # an existing topological path inside the combined reviewed footprint,
        # not discovery of a nearby/disconnected crossing.
        adjacency=defaultdict(list)
        for e in byid.values():
            if e['kind'] in ('sidewalk','shared_way'):
                adjacency[e['from']].append((e['to'],e['id']))
                adjacency[e['to']].append((e['from'],e['id']))
        port_owner={p['nodeId']:p['previousJunctionId'] for p in r['ports']}
        for start in port_nodes:
            for current,eid in adjacency[start]:
                previous=start;chain=[eid];length=math.dist(node_point(start),node_point(current))
                seen={start}
                while current not in port_nodes and current not in seen and len(adjacency[current])==2 and length<=100:
                    if math.dist(node_point(current),center)>65:break
                    seen.add(current)
                    other,step=next((n,e) for n,e in adjacency[current] if n!=previous)
                    length+=math.dist(node_point(current),node_point(other));chain.append(step)
                    previous,current=current,other
                if current in port_nodes and port_owner[current]!=port_owner[start] and length<=100:
                    internal.update(chain)
        if not internal:
            raise ValueError('reviewed junction halves have no existing internal topology: '+str(ids))
        r['ports']=[p for p in r['ports'] if p['edgeId'] not in internal]
        for eid in sorted(internal):retained.pop(eid,None);removed[eid]=byid[eid]
        r['centerMeters']=center
        # Connector port labels are about to be reconstructed from actual rays.
        r['connectors']=[{**c,'fromPort':q['id']+'/'+c['fromPort'],'toPort':q['id']+'/'+c['toPort']}
                         for q in rs for c in q['connectors']]
        r['rawNodeIds']=sorted({n for q in rs for n in q.get('rawNodeIds',[])})
        r['retiredEdgeIds']=sorted({e for q in rs for e in q.get('retiredEdgeIds',[])}|internal)
        r['mergedJunctionIds']=ids
        merges.append({'id':r['id'],'previousIds':ids,'retiredInternalApproachIds':sorted(internal)})
        merged.append(r)
    records=merged
    cuts=defaultdict(list);aliases={};report={'removedSidewalkIds':sorted(eid for eid,e in removed.items() if e['kind']=='sidewalk'),'portMigrations':[],
                                          'nodeMigrations':[],'retiredConnectorIds':[],
                                          'mergedJunctions':merges}
    removed_ports=set()
    # Review geometry already establishes the physical approach. Keep its two
    # extreme sidewalks, never its median ports. A missing opposite exterior is
    # explicitly projected onto a policy-owned counterpart, not a nearby road.
    for record in records:
        all_ports={p['id']:p for p in record['ports']}
        next_ports=[]
        physical_approaches=[]
        for ai,arm in enumerate(_arms(record,byid,nodes)):
            approach_id=f'physical-arm-{ai}'
            for p in arm:p['port']['approachId']=approach_id
            physical_approaches.append({'id':approach_id,'sourcePortIds':[p['port']['id'] for p in arm],
                                        'ray':arm[0]['ray']})
            kept=[p for p in arm if p['port']['edgeId'] in retained]
            if len(arm)==1:
                next_ports.append(arm[0]['port']);continue
            if len(kept)>=2:
                keep=[kept[0]['port'],kept[-1]['port']]
                # A reviewed pair supersedes any unpaired-ramp provisional side.
                for entry in kept[1:-1]:
                    eid=entry['port']['edgeId'];removed[eid]=retained.pop(eid)
                    report['removedSidewalkIds'].append(eid)
                next_ports.extend(keep)
                removed_ports.update(p['port']['nodeId'] for p in arm if p['port'] not in keep)
                continue
            if len(kept)!=1:
                raise ValueError('physical approach lost both exteriors: '+record['id'])
            existing=kept[0]['port'];next_ports.append(existing)
            gone=next(p for p in arm if p['port'] is not existing)
            old=gone['port'];spec=owner(byid[old['edgeId']])
            candidates=[]
            for way in spec.get('counterpartWayIds',[]):
                for e in by_way[way]:
                    if e['id'] not in retained:continue
                    dist,p,t=_project(node_point(old['nodeId']),e['pathMeters'])
                    if not opposite_carriageway(byid[old['edgeId']],e,p):continue
                    a,b=e['pathMeters'];unit=[b[i]-a[i] for i in (0,1)]
                    length=math.hypot(*unit);dot=sum(unit[i]/length*gone['ray'][i] for i in (0,1))
                    if dist>65:continue
                    if math.dist(p,record['centerMeters'])>90:continue
                    candidates.append((dist,e['id'],p,t,dot))
            if not candidates:
                raise ValueError('missing policy-owned opposite sidewalk at '+record['id']+' / '+old['id'])
            dist,target,point,t,dot=min(candidates)
            point=[round(v,3) for v in point]
            nodes[old['nodeId']]={**nodes[old['nodeId']],'xMeters':point[0],'yMeters':point[1]}
            next_ports.append(old)
            cuts[target].append({'port':old,'point':point,'t':t,'outward':dot>0,
                                 'junctionId':record['id'],'center':record['centerMeters']})
            report['portMigrations'].append({'junctionId':record['id'],'portId':old['id'],
                'oldEdgeId':old['edgeId'],'targetOuterEdgeId':target,'distanceMeters':round(dist,3)})
        record['ports']=next_ports
        record['physicalApproaches']=physical_approaches
        waits={}
        for approach in physical_approaches:
            original=set(approach['sourcePortIds'])
            values={c.get('waitSeconds',20) for c in record['connectors'] if c['kind']=='crossing' and
                    c['fromPort'] in original and c['toPort'] in original}
            if len(values)>1:raise ValueError('conflicting physical crossing wait: '+record['id'])
            waits[approach['id']]=next(iter(values),20)
        record['crossingWaitByApproach']=waits
        record.pop('terminalMedianPortIds',None)
        # All old junction links are replaced as one model; no median bypass.
        for c in record['connectors']:
            retained.pop(c['edgeId'],None);report['retiredConnectorIds'].append(c['edgeId'])
        record['connectors']=[]
    for eid,requests in cuts.items():
        edge=retained.pop(eid)
        groups=defaultdict(list)
        for c in requests:groups[c['junctionId']].append(c)
        intervals=[]
        for jid,cs in groups.items():
            if len(cs)==2 and cs[0]['outward']!=cs[1]['outward']:
                lo,hi=sorted(cs,key=lambda c:c['t']);intervals.append((lo['t'],hi['t'],lo,hi))
            elif len(cs)==1:
                c=cs[0];inside_end=edge['pathMeters'][0 if c['outward'] else -1]
                if math.dist(inside_end,c['center'])>55:
                    raise ValueError('opposite exterior continues through unhandled arm: '+jid+' / '+eid)
                intervals.append((0,c['t'],None,c) if c['outward'] else (c['t'],1,c,None))
            else:raise ValueError('overlapping projected exterior ports: '+eid)
        intervals.sort(key=lambda r:r[0]);cursor=0;start_node=edge['from'];a,b=edge['pathMeters']
        for index,(lo,hi,lo_req,hi_req) in enumerate(intervals+[(1,1,None,None)]):
            if lo<cursor-1e-6:raise ValueError('overlapping arterial junction cuts: '+eid)
            end_node=lo_req['port']['nodeId'] if lo_req else edge['to']
            if (lo-cursor)*math.dist(a,b)>=.05:
                new_id=f'{eid}:exterior:{index}'
                retained[new_id]={**edge,'id':new_id,'from':start_node,'to':end_node,
                                  'pathMeters':[node_point(start_node),node_point(end_node)],'originalEdgeId':eid}
                for req in requests:
                    if req['port']['nodeId'] in (start_node,end_node):
                        req['port']['sourceApproachEdgeId']=req['port']['edgeId'];req['port']['edgeId']=new_id
            cursor=hi;start_node=hi_req['port']['nodeId'] if hi_req else edge['from']
        for record in records:
            for p in record['ports']:
                if p['edgeId']==eid:
                    choices=[e for e in retained.values() if e.get('originalEdgeId')==eid and
                             p['nodeId'] in (e['from'],e['to'])]
                    if len(choices)!=1:raise ValueError('arterial cut removes another owned port: '+record['id']+' / '+eid)
                    p['sourceApproachEdgeId']=eid;p['edgeId']=choices[0]['id']
    # Recreate corners and full-width crossings from the migrated outer ports.
    for record in records:
        ports={p['id']:p for p in record['ports']}
        groups=_arms(record,retained,nodes,physical=True)
        if len(groups) not in (3,4):raise ValueError('arterial junction no longer has 3/4 arms: '+record['id']+' '+str([[p['port']['id'] for p in arm] for arm in groups]))
        connectors=[]
        for i,arm in enumerate(groups):
            following=groups[(i+1)%len(groups)]
            connectors.append(('turn',arm[-1]['port'],following[0]['port'],f'exterior-corner-{i}'))
            if len(arm)>1:
                connectors.append(('crossing',arm[0]['port'],arm[-1]['port'],f'exterior-cross-{i}'))
        for kind,p,q,label in connectors:
            eid=f'manual-junction:{record["id"]}:{label}'
            edge={'id':eid,'from':p['nodeId'],'to':q['nodeId'],'kind':kind,
                  'pathMeters':[node_point(p['nodeId']),node_point(q['nodeId'])],
                  'annotationId':record['id'],'annotationKind':'junction',
                  'verificationStatus':record['verificationStatus'],'annotationSource':record['source']}
            if math.dist(*edge['pathMeters'])<.05:raise ValueError('exterior corner collapsed: '+eid)
            if kind=='crossing':edge['waitSeconds']=record['crossingWaitByApproach'][p['approachId']]
            retained[eid]=edge
            record['connectors'].append({'id':label,'kind':kind,'fromPort':p['id'],'toPort':q['id'],
                                        'edgeId':eid,**({'waitSeconds':edge['waitSeconds']} if kind=='crossing' else {})})
        record['exteriorOnly']=True
        record['retiredEdgeIds']=sorted(set(record.get('retiredEdgeIds',[])) |
            {e for e in report['retiredConnectorIds'] if e.startswith('manual-junction:'+record['id']+':')})
    # Unreviewed mid-block connections retain their explicit cost/permission.
    # An inward node moves to the policy's opposite physical exterior. Splitting
    # uses only the recorded counterpart ways, never unrestricted neighbours.
    incidence=defaultdict(list)
    for e in removed.values():
        if e['kind']!='sidewalk':continue
        for nid in (e['from'],e['to']):incidence[nid].append(e)
    projected=defaultdict(list)
    protected={p['nodeId'] for r in records for p in r['ports']}
    for edge in list(retained.values()):
        if edge['kind']=='sidewalk' or edge.get('annotationKind')=='junction':continue
        for endpoint in ('from','to'):
            nid=edge[endpoint]
            if nid not in incidence:continue
            if nid in aliases:continue
            if nid in removed_ports:raise ValueError('median junction port retains external connection: '+nid)
            old=incidence[nid][0];spec=owner(old)
            if not spec:raise ValueError('missing removed-edge ownership')
            candidates=[]
            for way in spec.get('counterpartWayIds',[]):
                for e in retained.values():
                    if e['kind']!='sidewalk' or raw[e['sourceMajorEdgeId']]['sourceWayId']!=way:continue
                    dist,p,t=_project(node_point(nid),e['pathMeters'])
                    if not opposite_carriageway(old,e,p):continue
                    if dist<=65:candidates.append((dist,e['id'],p,t))
            if not candidates:
                raise ValueError('unhandled attached inward node: '+nid+' / '+old['id'])
            dist,target,point,t=min(candidates)
            candidate=retained[target]
            if math.dist(point,node_point(candidate['from']))<.05:alias=candidate['from']
            elif math.dist(point,node_point(candidate['to']))<.05:alias=candidate['to']
            else:
                old_anchor=next((id for _,id in projected[target] if math.dist(point,node_point(id))<.05),None)
                alias=old_anchor or f'exterior-anchor:{target}:{t:.8f}'
                if alias not in nodes:
                    point=[round(v,3) for v in point]
                    nodes[alias]={'id':alias,'xMeters':point[0],'yMeters':point[1]}
                    projected[target].append((t,alias))
            aliases[nid]=alias
            report['nodeMigrations'].append({'oldNodeId':nid,'newNodeId':alias,'targetEdgeId':target,
                                            'distanceMeters':round(dist,3)})
    for eid,anchors in projected.items():
        edge=retained.pop(eid);sequence=[edge['from'],*(n for _,n in sorted(set(anchors))),edge['to']]
        for index,(first,last) in enumerate(zip(sequence,sequence[1:])):
            if first==last:continue
            new_id=f'{eid}:anchor:{index}'
            retained[new_id]={**edge,'id':new_id,'from':first,'to':last,
                              'pathMeters':[node_point(first),node_point(last)],'originalEdgeId':eid}
        for record in records:
            for p in record['ports']:
                if p['edgeId']==eid:
                    p['sourceApproachEdgeId']=eid
                    p['edgeId']=next(e['id'] for e in retained.values() if e.get('originalEdgeId')==eid and p['nodeId'] in (e['from'],e['to']))
    # Preview branch endpoints are vehicle-centre proxies, not places to walk
    # into and back out of the roadway. Move the dedicated branch attachment
    # onto its selected exterior; do not create a shared central transfer.
    for edge in retained.values():
        if edge['id'].startswith('synthetic-turn:') and edge['from'].startswith('shared:'):
            stub=edge['from'];target=aliases.get(edge['to'],edge['to'])
            incident=[e for e in retained.values() if stub in (e['from'],e['to'])]
            if len(incident)==2 and any(e['kind']=='shared_way' for e in incident):
                aliases[stub]=target
    result=[]
    for e in retained.values():
        for endpoint,index in (('from',0),('to',-1)):
            if e[endpoint] in aliases:
                e[endpoint]=aliases[e[endpoint]];e['pathMeters'][index]=node_point(e[endpoint])
        if e['from']==e['to']:
            report['retiredConnectorIds'].append(e['id']);continue
        result.append(e)
    exterior_owners=defaultdict(list)
    for e in result:
        if e['kind']=='sidewalk':
            for nid in (e['from'],e['to']):exterior_owners[nid].append(e)
    converted=[]
    for edge in result:
        if edge['kind'] not in ('shared_way','turn') or edge.get('annotationKind')=='junction':continue
        opposite=False
        for a in exterior_owners[edge['from']]:
            for b in exterior_owners[edge['to']]:
                sa,sb=owner(a),owner(b)
                if sa['sourceEdgeId']==sb['sourceEdgeId'] and a['side']!=b['side']:
                    opposite=True
                elif sb['sourceWayId'] in sa.get('counterpartWayIds',[]) and opposite_carriageway(a,b,node_point(edge['to'])):
                    opposite=True
        if opposite:
            edge['kind']='crossing'
            edge.pop('sharedWayType',None);edge.pop('widthMeters',None)
            edge['verificationStatus']=policy['verificationStatus']
            converted.append(edge['id'])
    report['convertedRoadInteriorConnectionIds']=converted
    report['verificationStatus']=policy['verificationStatus']
    report['sourceEdgeCount']=len(specs)
    report['nodeAliases']=aliases
    report['supersededDividedRoadSectionIds']=[s['id'] for s in source.get('dividedRoadSections',[])]
    return result,records,report
