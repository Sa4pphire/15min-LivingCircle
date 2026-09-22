# C++ Isochrone Engine

This directory contains the pure spatial-computation component of the demo.
It must not call Baidu APIs or read credentials.

## Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
ctest --test-dir build --output-on-failure
```

## Contract

- Input: one JSON object on standard input.
- Output: one JSON object on standard output.
- Logs: standard error only.
- Coordinates: local meter offsets from the selected center.

The grid, IDW, Marching Squares, ring-building and simplification modules are
already separated. JSON contract parsing and end-to-end orchestration are the
next implementation tasks.
