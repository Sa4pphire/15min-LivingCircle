# Test Plan

## Automated

- Python API schema and analysis task lifecycle.
- Walking graph right turns without waiting, crossings with 20-second waiting, and disconnected geometric intersections.
- Retry, timeout, quota-error and partial-result behavior.
- POI UID deduplication and category filtering.
- C++ health check, invalid graph, origin snapping, 900-second edge clipping and display polygon generation.
- Contract v2 fixture round trip from Python to C++ when testing resumes; the synthetic fixture does not verify a real area.
- Frontend production build and Docker image build.

## Manual acceptance

- Primary preset: 上海市杨浦区新江湾城街道.
- Eight reachable street segments and four crossing locations are checked against the manually annotated map.
- Twenty returned POIs are checked for category correctness and duplicates.
- Ten blind-zone grid cells are checked against the two nearest facilities.
- Live analysis target: under 30 seconds; cached preset target: under 2 seconds.
- The UI remains usable and explains failures when Baidu or the engine is unavailable.
