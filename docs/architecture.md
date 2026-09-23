# Architecture

## Responsibilities

### Browser

- Select a center point.
- Poll analysis progress.
- Render exact reachable walkways and an approximate display polygon.

### FastAPI service

- Own all Baidu credentials and Web API calls used by other analysis features.
- Enforce batching, rate limits, retries and cache policy.
- Clean POIs, calculate coverage metrics and build the report.
- Invoke the C++ engine with a versioned JSON contract.

### C++ engine

- Accept a manually annotated local walking graph and algorithm parameters.
- Run Dijkstra on explicit sidewalk, turn and crossing edges; crossing edges add 20 seconds.
- Return exact reachable edge portions, frontier points and a buffered display polygon.
- Never call Baidu APIs or read credentials.

## Coordinate policy

- Public and provider-facing coordinates use BD-09 longitude/latitude.
- Spatial computation uses local meter offsets from the annotated network origin.
- Time values use seconds; distance values use meters.
- Python converts the local result to BD-09 map coordinates and returns GeoJSON-shaped features with coordinate metadata.
- A display polygon is approximate and must not be used for facility reachability decisions.

## Failure policy

- External requests have bounded retries and explicit timeouts.
- Partial data is returned only with user-visible warnings.
- Cached preset results are labeled as cached, never presented as live.
- The C++ engine writes machine-readable output only to stdout and logs to stderr.
- If the real annotated network is absent or the center is outside its supported bounds, the API returns `UNSUPPORTED_AREA`.
