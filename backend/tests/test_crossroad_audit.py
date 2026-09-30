"""All-source four-way inventory and corner/crossing rule regressions."""

import asyncio
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path

import pytest

from app import engine
from app.crossroad_audit import audit_crossroads, audit_explicit_junction, discover_crossroads, group_directions
from app.synthetic_converter import convert_preview_graph
from test_reviewed_junctions import _distances, _local_graph, _payload, cpp_binary  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
ANNOTATIONS = json.loads((ROOT/'data/networks/synthetic-preview.annotations.json').read_text('utf-8'))
NEW_IDS = [r['id'] for r in ANNOTATIONS['junctions'] if r['id'].startswith('crossroad-')]


def fixture(*, shared=False, asymmetric=False) -> dict:
    points = {'c':(0,0),'n':(80,80),'nw':(-80,80),'s':(-80,-80),'se':(80,-80)}
    pairs = [('n','c','n'),('nw','nw','c'),('s','s','c'),('se','c','se')]
    if asymmetric:
        points.update(d=(0,-16),n2=(80,64),s=(-80,-96))
        pairs[2]=('s','s','d')
        pairs.extend([('n2','d','n2'),('inside','c','d')])
    return {'schemaVersion':1,'kind':'synthetic-road-graph','coordinateSystem':'preview-local-v1',
            'nodes':[{'id':n,'x':x,'y':-y} for n,(x,y) in points.items()],
            'edges':[{'id':name,'from':a,'to':b,'kind':'roadLocal' if shared or (asymmetric and name=='s') else 'roadMajor',
                      'sourceWayId':i+1} for i,(name,a,b) in enumerate(pairs)],
            'selectionBoundary':[[[-200,-200],[200,-200],[200,200],[-200,200],[-200,-200]]]}


def converted(*, asymmetric=False) -> tuple[dict,dict]:
    source=fixture(asymmetric=asymmetric)
    proposal=discover_crossroads(source)[0]['proposal']
    if asymmetric:
        valid=[]
        for middle in combinations(range(4),2):
            candidate=deepcopy(proposal)
            candidate['medianConnections']=[{'id':f'median-{i}','fromPort':f'arm-0-{i}',
                                            'toPort':'arm-2-0','waitSeconds':20} for i in middle]
            try:
                graph=convert_preview_graph(source,[121.505,31.333],junction_annotations=[candidate])
            except ValueError:
                continue
            valid.append((graph,candidate))
        assert len(valid)==1
        return valid[0]
    return convert_preview_graph(source,[121.505,31.333],junction_annotations=[proposal]),proposal


def test_circular_grouping_does_not_split_north_or_chain_a_fork():
    groups=group_directions([{'angle':v} for v in [358,2,90,180,270]])
    assert sorted(len(g) for g in groups)==[1,1,1,2]
    assert len(group_directions([{'angle':v} for v in [0,20,40,180]]))==3


def test_shared_crossroad_has_no_artificial_wait_and_inventory_is_read_only():
    source=fixture(shared=True)
    graph=convert_preview_graph(source,[121.505,31.333])
    snapshot=deepcopy((source,graph))
    report=audit_crossroads(source,graph,graph)
    assert report['statuses']=={'shared_junction_pass':1}
    assert all(e['kind']=='shared_way' for e in graph['edges'])
    assert (source,graph)==snapshot


def test_unconnected_geometric_crossing_never_generates_a_proposal():
    source=fixture(shared=True)
    source['nodes']=[n for n in source['nodes'] if n['id']!='c']
    source['edges']=[{'id':'a','from':'n','to':'s','kind':'roadLocal','sourceWayId':1},
                     {'id':'b','from':'nw','to':'se','kind':'roadLocal','sourceWayId':2}]
    assert discover_crossroads(source)==[]


def test_only_existing_short_edges_can_group_two_carriageway_vertices():
    source=fixture(asymmetric=True)
    found=discover_crossroads(source)
    assert len(found)==1 and found[0]['rawNodeIds']==['c','d']
    source['edges']=[e for e in source['edges'] if e['id']!='inside']
    assert all(set(c['rawNodeIds'])!= {'c','d'} for c in discover_crossroads(source))


def test_degree_two_internal_merge_is_absorbed_but_disconnected_point_is_not():
    xy={'nw':(-5,5),'ne':(5,5),'sw':(-5,-5),'se':(5,-5),'merge':(12,0),
        'nearby':(0,1),'n1':(-5,80),'n2':(5,80),'e1':(80,5),'e2':(80,-5),
        's1':(-5,-80),'s2':(5,-80),'w1':(-80,5),'w2':(-80,-5)}
    pairs=[('nw','ne'),('ne','se'),('se','sw'),('sw','nw'),
           ('ne','merge'),('merge','se'),('nw','n1'),('ne','n2'),('ne','e1'),('se','e2'),
           ('sw','s1'),('se','s2'),('nw','w1'),('sw','w2')]
    source=fixture()
    source['nodes']=[{'id':n,'x':x,'y':-y} for n,(x,y) in xy.items()]
    source['edges']=[{'id':f'e{i}','from':a,'to':b,'kind':'roadMajor','sourceWayId':i+1}
                      for i,(a,b) in enumerate(pairs)]
    candidates=discover_crossroads(source)
    assert len(candidates)==1
    assert 'merge' in candidates[0]['rawNodeIds'] and 'nearby' not in candidates[0]['rawNodeIds']
    graph=convert_preview_graph(source,[121.505,31.333],junction_annotations=[candidates[0]['proposal']])
    record=graph['sourceGraph']['manualJunctionAnnotations'][0]
    assert audit_explicit_junction(graph,record)['pass']


@pytest.mark.parametrize('mutation',['remove_turn','remove_crossing','wrong_turn','free_bypass',
                                     'approach_stub_bypass','turn_wait','duplicate','old_center',
                                     'retired_edge','bad_wait','stale_record'])
def test_rule_checker_finds_semantic_errors_not_just_edge_counts(mutation):
    graph,_=converted()
    record=graph['sourceGraph']['manualJunctionAnnotations'][0]
    turns=[e for e in graph['edges'] if e['kind']=='turn']
    crosses=[e for e in graph['edges'] if e['kind']=='crossing']
    if mutation in ('remove_turn','remove_crossing'):
        target=turns[0] if mutation=='remove_turn' else crosses[0]
        graph['edges'].remove(target)
    elif mutation=='wrong_turn':
        turns[0]['to']=crosses[0]['to']
    elif mutation=='free_bypass':
        graph['edges'].append({**crosses[0],'id':'bad-free','kind':'turn'})
    elif mutation=='approach_stub_bypass':
        # Correct corner/crossing counts can still hide a free transfer through
        # the far endpoints of two approach stubs (the real degree-two bug).
        port_ids = (crosses[0]['from'], crosses[0]['to'])
        ports = {p['nodeId']:p for p in record['ports']}
        by_edge = {e['id']:e for e in graph['edges']}
        far = []
        for nid in port_ids:
            edge = by_edge[ports[nid]['edgeId']]
            far.append(edge['to'] if edge['from']==nid else edge['from'])
        keep, removed = far
        point = next([n['xMeters'],n['yMeters']] for n in graph['nodes'] if n['id']==keep)
        for edge in graph['edges']:
            for key,index in (('from',0),('to',-1)):
                if edge[key]==removed:
                    edge[key]=keep
                    edge['pathMeters'][index]=point[:]
        graph['nodes']=[n for n in graph['nodes'] if n['id']!=removed]
    elif mutation=='turn_wait':
        turns[0]['waitSeconds']=20
    elif mutation=='duplicate':
        graph['edges'].append({**turns[0],'id':'duplicate-turn'})
    elif mutation=='old_center':
        graph['edges'].append({'id':'old-join','from':record['ports'][0]['endpointNodeId'],
                               'to':record['ports'][1]['nodeId'],'kind':'turn'})
    elif mutation=='retired_edge':
        record['retiredEdgeIds'].append(turns[0]['id'])
    elif mutation=='bad_wait':
        crosses[0]['waitSeconds']=float('nan')
    else:
        record['ports'][0]['edgeId']='missing'
    check = audit_explicit_junction(graph,record)
    assert not check['pass']
    if mutation=='approach_stub_bypass':
        assert 'free_crossing_bypass' in check['issues']


def test_asymmetric_median_is_never_auto_joined_and_explicit_join_is_charged():
    source=fixture(asymmetric=True)
    proposal=discover_crossroads(source)[0]['proposal']
    with pytest.raises(ValueError,match='explicit crossings'):
        convert_preview_graph(source,[121.505,31.333],junction_annotations=[proposal])
    graph,proposal=converted(asymmetric=True)
    record=graph['sourceGraph']['manualJunctionAnnotations'][0]
    assert audit_explicit_junction(graph,record)['pass']
    assert len(record['ports'])==9
    assert len([e for e in graph['edges'] if e['kind']=='crossing'])==5
    for link in proposal['medianConnections']:
        ports={p['id']:p['nodeId'] for p in record['ports']}
        assert ports[link['toPort']] not in _distances(_local_graph(graph,record),ports[link['fromPort']],crossings=False)
    invalid=deepcopy(proposal)
    invalid['medianConnections'][0]['toPort']='arm-1-0'
    with pytest.raises(ValueError,match='explicit crossings'):
        convert_preview_graph(source,[121.505,31.333],junction_annotations=[invalid])


@pytest.fixture(scope='module')
def batch_graph():
    return json.loads((ROOT/'data/networks/synthetic-preview.json').read_text('utf-8'))


def test_whole_inventory_covers_every_raw_four_way_vertex(batch_graph):
    source=json.loads((ROOT/'frontend/src/data/demoRoadGraph.local.json').read_text('utf-8'))
    original=deepcopy(batch_graph)
    # The local divided-road closure names a reviewed junction; omit the
    # dependent section when constructing the unreviewed baseline.
    baseline=convert_preview_graph({**source, 'dividedRoadSections': []},[121.505,31.333],ANNOTATIONS['crossings'])
    report=audit_crossroads(source,batch_graph,baseline)
    incidence={}
    for edge in source['edges']:
        if edge['kind']!='inferredJunction':
            for key in ('from','to'): incidence[edge[key]]=incidence.get(edge[key],0)+1
    four={n for n,d in incidence.items() if d==4}
    inventoried={n for c in report['crossroads'] for n in c['rawNodeIds'] if n in four}
    assert inventoried==four
    assert report['rawFourWayVertexCount']==len(four)
    assert 'missing_explicit_model' not in report['statuses']
    assert 'partial_explicit_model' not in report['statuses']
    assert all(check['pass'] for check in report['explicitJunctionChecks'])
    assert batch_graph==original
    assert NEW_IDS, 'checked-in repair batch must not be empty'


@pytest.mark.parametrize('record_id',NEW_IDS)
def test_every_new_crossroad_has_four_correct_corners_without_free_crossing(batch_graph,record_id):
    record=next(r for r in batch_graph['sourceGraph']['manualJunctionAnnotations'] if r['id']==record_id)
    check=audit_explicit_junction(batch_graph,record)
    assert check['pass'] and check['approaches']==4 and check['turns']==4
    assert record['verificationStatus']=='geometry_inferred_unverified'
    local=_local_graph(batch_graph,record)
    edges={e['id']:e for e in local['edges']}
    for c in record['connectors']:
        edge=edges[c['edgeId']]
        no_wait=_distances(local,edge['from'],crossings=False)
        if edge['kind']=='crossing':
            assert edge['waitSeconds']==20
            assert edge['to'] not in no_wait
        else:
            assert edge['to'] in no_wait and 'waitSeconds' not in edge


@pytest.mark.parametrize('record_id',NEW_IDS)
def test_cpp_each_new_crossroad_matches_route_oracle_with_and_without_crossings(batch_graph,record_id,cpp_binary):
    record=next(r for r in batch_graph['sourceGraph']['manualJunctionAnnotations'] if r['id']==record_id)
    local=_local_graph(batch_graph,record)
    ports={p['id']:p['nodeId'] for p in record['ports']}
    for include_crossings in (True,False):
        variant={**local,'edges':[e for e in local['edges'] if include_crossings or e['kind']!='crossing']}
        payload,start=_payload(variant,record)
        expected=_distances(variant,start)
        result=asyncio.run(engine.run_engine(payload))
        for facility in result['facilityTravelTimes']:
            node_id = ports[facility['id']]
            if node_id in expected:
                assert facility['travelTimeSeconds'] == pytest.approx(expected[node_id])
            else:
                assert facility['travelTimeSeconds'] is None


@pytest.mark.parametrize('record_id',NEW_IDS)
def test_cpp_full_graph_new_ports_match_global_route_oracle(batch_graph,record_id,cpp_binary):
    record=next(r for r in batch_graph['sourceGraph']['manualJunctionAnnotations'] if r['id']==record_id)
    payload,start=_payload(batch_graph,record,threshold=90)
    expected=_distances(batch_graph,start)
    ports={p['id']:p['nodeId'] for p in record['ports']}
    result=asyncio.run(engine.run_engine(payload))
    for facility in result['facilityTravelTimes']:
        node_id = ports[facility['id']]
        if node_id in expected:
            assert facility['travelTimeSeconds'] == pytest.approx(expected[node_id])
        else:
            assert facility['travelTimeSeconds'] is None
