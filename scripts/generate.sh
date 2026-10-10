#!/usr/bin/env sh
# Regenerates src/my_adapter/generated/ from proto/adapter.proto.
# Run it again whenever the protocol file changes. Needs: pip install -e ".[dev]"
set -eu
cd "$(dirname "$0")/.."
python -m grpc_tools.protoc -Iproto \
  --python_out=src/my_adapter/generated \
  --grpc_python_out=src/my_adapter/generated \
  proto/adapter.proto
# protoc writes "import adapter_pb2"; make it a package-relative import.
python - <<'PY'
import pathlib, re
p = pathlib.Path("src/my_adapter/generated/adapter_pb2_grpc.py")
p.write_text(re.sub(r"^import adapter_pb2 as", "from . import adapter_pb2 as", p.read_text(), flags=re.M))
PY
echo "generated"
