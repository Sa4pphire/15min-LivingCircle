"""Measure full 15-minute requests on the current synthetic walking graph.

Timings include Python JSON serialization, process startup and C++ output
parsing. They are local observations, not a real-world precision benchmark.
"""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app import engine, network  # noqa: E402
from app.schemas import CenterPoint  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="unspecified")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--pipeline", action="store_true", help="Include local cached POI enrichment, never online fetching.")
    args = parser.parse_args()
    if args.repeats < 1 or not args.engine.is_file():
        raise ValueError("existing engine and positive repeats are required")
    engine.settings = replace(engine.settings, cpp_engine_path=args.engine.resolve())
    observations = []
    points = [("west", 121.501132, 31.333337), ("off-road-center", 121.505, 31.333),
              ("north-center", 121.507, 31.336)]
    for name, lng, lat in points:
        center = CenterPoint(lng=lng, lat=lat, coordType="wgs84ll")
        payload, _ = network.load_engine_request(center, network_path=ROOT / "data/networks/synthetic-preview.json")
        for repetition in range(args.repeats):
            start = time.perf_counter()
            details = {}
            if args.pipeline:
                from app import main as api
                from app.schemas import AnalysisState, Progress
                identifier = f"benchmark-{name}-{repetition}"
                api._analyses[identifier] = AnalysisState(analysisId=identifier, status="queued",
                                                        progress=Progress(stage="queued", percent=0))
                payload, metadata = network.load_engine_request(center, network_path=ROOT / "data/networks/synthetic-preview.json")
                try:
                    asyncio.run(api._run_analysis(identifier, payload, metadata, center, True))
                    state = api._analyses[identifier]
                    if state.status != "completed":
                        raise ValueError(state.error)
                    details = {"firstResultSeconds": state.timingsMs["firstResult"] / 1000,
                               "poiStatus": state.poiStatus, "timingsMs": state.timingsMs,
                               "apiRequests": state.result["metadata"]["poi"].get("apiRequests", 0),
                               "poiCandidates": len(state.result.get("poiFacilities", {}).get("features", [])),
                               "buildMode": state.result["metadata"]["engineBuildMode"]}
                    result = {"reachableEdges": state.result["reachableWalkways"]["features"],
                              "displayGeometryMeters": state.result["isochrone"]["geometry"]}
                finally:
                    api._analyses.pop(identifier, None)
                    api._poi_routes.contexts.pop(identifier, None)
            else:
                result = asyncio.run(engine.run_engine(payload))
                details = {"buildMode": result.get("diagnostics", {}).get("buildMode", "unknown"),
                           "timingsMs": result.get("diagnostics", {}).get("timingsMs", {})}
            seconds = time.perf_counter() - start
            if not result["reachableEdges"] or not result["displayGeometryMeters"]["coordinates"]:
                raise ValueError("benchmark returned empty walking network or display geometry")
            observations.append({"point": name, "repetition": repetition+1, "seconds": round(seconds, 6),
                                 "reachableEdges": len(result["reachableEdges"]),
                                 "polygons": len(result["displayGeometryMeters"]["coordinates"]), **details})
    seconds = [r["seconds"] for r in observations]
    report = {"configuration": args.label, "synthetic": True, "nodes": len(payload["nodes"]),
              "edges": len(payload["edges"]), "thresholdSeconds": 900, "samples": observations,
              "medianSeconds": statistics.median(seconds), "maxSeconds": max(seconds),
              "engineCallLimitSeconds": 25, "allUnderCallLimit": max(seconds) < 25,
              "scope": ("Local backend pipeline with cache-only POIs; excludes HTTP and browser rendering."
                        if args.pipeline else "Local Python-to-C++ invocation; excludes HTTP, POI acquisition and browser rendering.")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
