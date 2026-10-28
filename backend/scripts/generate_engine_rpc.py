"""Explicitly generate optional bindings; never installs dependencies or runs services."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
import sys


EXPECTED_VERSIONS = {
    "grpcio": "1.84.0",
    "grpcio-tools": "1.84.0",
    "protobuf": "7.36.2",
}


def main() -> int:
    for package, expected in EXPECTED_VERSIONS.items():
        try:
            installed = version(package)
        except PackageNotFoundError:
            print("Install backend/requirements-rpc.txt before generating RPC bindings.",
                  file=sys.stderr)
            return 2
        if installed != expected:
            print(f"Expected {package}=={expected}; install backend/requirements-rpc.txt.",
                  file=sys.stderr)
            return 2

    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    proto = root / "cpp-engine/rpc/engine_service.proto"
    if not proto.is_file():
        print("Cannot find cpp-engine/rpc/engine_service.proto.", file=sys.stderr)
        return 2
    output = backend / "app/engine_rpc_generated"
    output.mkdir(parents=True, exist_ok=True)
    # A virtual proto path makes the generated grpc module import
    # app.engine_rpc_generated.engine_service_pb2 without patching its code.
    result = subprocess.run([
        sys.executable, "-m", "grpc_tools.protoc",
        f"-Iapp/engine_rpc_generated={proto.parent}",
        f"--python_out={backend}", f"--grpc_python_out={backend}", str(proto),
    ], check=False)
    if result.returncode == 0:
        print("Generated app.engine_rpc_generated bindings (ignored by Git).")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
