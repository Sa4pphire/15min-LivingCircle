add_executable(test_idw test_idw.cpp)
target_link_libraries(test_idw PRIVATE isochrone_core)
add_test(NAME idw COMMAND test_idw)

add_executable(test_marching_squares test_marching_squares.cpp)
target_link_libraries(test_marching_squares PRIVATE isochrone_core)
add_test(NAME marching_squares COMMAND test_marching_squares)

add_executable(test_ring_builder test_ring_builder.cpp)
target_link_libraries(test_ring_builder PRIVATE isochrone_core)
add_test(NAME ring_builder COMMAND test_ring_builder)

add_test(NAME engine_health COMMAND isochrone_engine --health)
set_tests_properties(
  engine_health
  PROPERTIES PASS_REGULAR_EXPRESSION "isochrone_engine"
)
