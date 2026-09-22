cmake_minimum_required(VERSION 3.20)
project(isochrone_engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(isochrone_engine src/main.cpp)

if(MSVC)
  target_compile_options(isochrone_engine PRIVATE /W4 /permissive-)
else()
  target_compile_options(isochrone_engine PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
  add_test(NAME engine_health COMMAND isochrone_engine --health)
  set_tests_properties(
    engine_health
    PROPERTIES PASS_REGULAR_EXPRESSION "isochrone_engine"
  )
endif()
