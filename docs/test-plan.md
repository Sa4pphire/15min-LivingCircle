# Test Plan

## Automated

- Python API schema and analysis task lifecycle.
- RouteMatrix batching at the 50-route boundary.
- Retry, timeout, quota-error and partial-result behavior.
- POI UID deduplication and category filtering.
- C++ health check, invalid input, sparse samples and contour extraction.
- Contract fixture round trip from Python to C++.
- Frontend production build and Docker image build.

## Manual acceptance

- Primary preset: 上海市杨浦区新江湾城街道.
- Eight contour boundary points are verified against walking-route results.
- Twenty returned POIs are checked for category correctness and duplicates.
- Ten blind-zone grid cells are checked against the two nearest facilities.
- Live analysis target: under 30 seconds; cached preset target: under 2 seconds.
- The UI remains usable and explains failures when Baidu or the engine is unavailable.
