# Architecture

## Responsibilities

### Browser

- Select a center point.
- Poll analysis progress.
- Render duration samples, isochrone, facilities and blind zones.

### FastAPI service

- Own all Baidu credentials and Web API calls.
- Enforce batching, rate limits, retries and cache policy.
- Clean POIs, calculate coverage metrics and build the report.
- Invoke the C++ engine with a versioned JSON contract.

### C++ engine

- Accept only local spatial samples and algorithm parameters.
- Interpolate the duration field and extract the 900-second contour.
- Return local-coordinate rings and diagnostics.
- Never call Baidu APIs or read credentials.

## Coordinate policy

- Public and provider-facing coordinates use BD-09 longitude/latitude.
- Spatial computation uses local meter offsets from the selected center.
- Time values use seconds; distance values use meters.
- Map geometry is returned as GeoJSON.

## Failure policy

- External requests have bounded retries and explicit timeouts.
- Partial data is returned only with user-visible warnings.
- Cached preset results are labeled as cached, never presented as live.
- The C++ engine writes machine-readable output only to stdout and logs to stderr.
