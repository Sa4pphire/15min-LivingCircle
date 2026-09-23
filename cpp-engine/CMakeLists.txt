cmake_minimum_required(VERSION 3.20)

project(
  isochrone_engine
  VERSION 0.1.0
  LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(
  isochrone_core
  src/grid.cpp
  src/idw.cpp
  src/engine.cpp
  src/json_io.cpp
  src/json_parser.cpp
  src/marching_squares.cpp
  src/ring_builder.cpp
  src/simplify.cpp
)

target_include_directories(
  isochrone_core
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

add_executable(isochrone_engine src/main.cpp)
target_link_libraries(isochrone_engine PRIVATE isochrone_core)

if(MSVC)
  target_compile_options(isochrone_core PRIVATE /W4 /permissive-)
  target_compile_options(isochrone_engine PRIVATE /W4 /permissive-)
else()
  target_compile_options(isochrone_core PRIVATE -Wall -Wextra -Wpedantic)
  target_compile_options(isochrone_engine PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()
