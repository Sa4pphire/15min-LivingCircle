if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "BUILD_RPC_SERVER currently supports Linux only. Leave it OFF to build the CLI on other platforms.")
endif()

# Require installed packages and their matching code generators. The default
# CLI build does not discover, download or depend on gRPC or Protobuf.
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(Threads REQUIRED)
foreach(rpc_dependency IN ITEMS
    protobuf::libprotobuf protobuf::protoc gRPC::grpc++ gRPC::grpc_cpp_plugin)
  if(NOT TARGET ${rpc_dependency})
    message(FATAL_ERROR "BUILD_RPC_SERVER requires the imported target ${rpc_dependency}. Install compatible Protobuf and gRPC config packages; dependencies are not downloaded automatically.")
  endif()
endforeach()

set(rpc_proto_file "${CMAKE_CURRENT_SOURCE_DIR}/rpc/engine_service.proto")
set(rpc_generated_dir "${CMAKE_CURRENT_BINARY_DIR}/rpc-generated")
set(rpc_generated_sources
    "${rpc_generated_dir}/engine_service.pb.cc"
    "${rpc_generated_dir}/engine_service.grpc.pb.cc")
set(rpc_generated_headers
    "${rpc_generated_dir}/engine_service.pb.h"
    "${rpc_generated_dir}/engine_service.grpc.pb.h")
add_custom_command(
  OUTPUT ${rpc_generated_sources} ${rpc_generated_headers}
  COMMAND ${CMAKE_COMMAND} -E make_directory "${rpc_generated_dir}"
  COMMAND "$<TARGET_FILE:protobuf::protoc>"
    "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/rpc"
    "--cpp_out=${rpc_generated_dir}"
    "--grpc_out=${rpc_generated_dir}"
    "--plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>"
    "${rpc_proto_file}"
  DEPENDS "${rpc_proto_file}" protobuf::protoc gRPC::grpc_cpp_plugin
  COMMENT "Generating the optional engine RPC protocol"
  VERBATIM
)
add_library(isochrone_rpc_protocol STATIC
    ${rpc_generated_sources} ${rpc_generated_headers})
target_include_directories(isochrone_rpc_protocol PUBLIC "${rpc_generated_dir}")
target_link_libraries(isochrone_rpc_protocol PUBLIC protobuf::libprotobuf gRPC::grpc++)
# Generated code needs the same language level without project warning flags.
target_compile_features(isochrone_rpc_protocol PUBLIC cxx_std_20)

add_executable(isochrone_rpc_server rpc/server_main.cpp rpc/engine_service.cpp)
# Generated headers must exist before compiling either executable, including
# when a parallel generator starts compilation before the final link step.
add_dependencies(isochrone_rpc_server isochrone_rpc_protocol)
target_link_libraries(isochrone_rpc_server PRIVATE
    isochrone_json isochrone_rpc_protocol Threads::Threads)
isochrone_target_defaults(isochrone_rpc_server)

add_executable(isochrone_rpc_client rpc/client_main.cpp)
add_dependencies(isochrone_rpc_client isochrone_rpc_protocol)
target_link_libraries(isochrone_rpc_client PRIVATE
    isochrone_rpc_protocol Threads::Threads)
isochrone_target_defaults(isochrone_rpc_client)
