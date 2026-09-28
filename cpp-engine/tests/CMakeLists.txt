add_executable(test_idw test_idw.cpp)
target_link_libraries(test_idw PRIVATE isochrone_core)
add_test(NAME idw COMMAND test_idw)

add_executable(test_marching_squares test_marching_squares.cpp)
target_link_libraries(test_marching_squares PRIVATE isochrone_core)
add_test(NAME marching_squares COMMAND test_marching_squares)

add_executable(test_ring_builder test_ring_builder.cpp)
target_link_libraries(test_ring_builder PRIVATE isochrone_core)
add_test(NAME ring_builder COMMAND test_ring_builder)

add_executable(test_road_enclosure test_road_enclosure.cpp)
target_link_libraries(test_road_enclosure PRIVATE isochrone_core)
add_test(NAME road_enclosure COMMAND test_road_enclosure)

add_executable(test_engine test_engine.cpp)
target_link_libraries(test_engine PRIVATE isochrone_core)
add_test(NAME graph_engine COMMAND test_engine)

add_executable(test_local_experiment test_local_experiment.cpp)
target_link_libraries(test_local_experiment PRIVATE isochrone_core)
add_test(NAME local_experiment COMMAND test_local_experiment)

add_executable(test_network_file test_network_file.cpp)
target_link_libraries(test_network_file PRIVATE isochrone_core)
target_compile_definitions(test_network_file PRIVATE
  ISOCHRONE_SYNTHETIC_NETWORK_PATH="${PROJECT_SOURCE_DIR}/../data/networks/synthetic-preview.json"
)
add_test(NAME network_file COMMAND test_network_file)

add_test(NAME engine_health COMMAND isochrone_engine --health)
add_test(NAME engine_synthetic_file COMMAND isochrone_engine --demo)
set_tests_properties(engine_synthetic_file PROPERTIES
  PASS_REGULAR_EXPRESSION "\"success\":true"
)
set_tests_properties(
  engine_health
  PROPERTIES PASS_REGULAR_EXPRESSION "isochrone_engine"
)
