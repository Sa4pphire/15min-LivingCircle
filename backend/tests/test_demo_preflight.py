"""Launcher preflight does not fabricate data or mislabel coordinate systems."""

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "demo_preflight", REPO_ROOT / "backend/scripts/demo_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_midpoint_uses_polyline_length():
    assert preflight.midpoint([[0, 0], [2, 0], [2, 6]]) == [2, 2]


def test_midpoint_rejects_zero_length():
    with pytest.raises(ValueError, match="zero length"):
        preflight.midpoint([[0, 0], [0, 0]])


def test_contract_uses_declared_bd09_demo_center():
    request, source = preflight.example_request(
        REPO_ROOT / "contracts/engine-local-experiment.input.example.json", local=True)
    assert source == "synthetic"
    assert request["center"]["coordType"] == "bd09ll"
    assert request["center"]["lng"] == 121.502102644
    assert request["originEdgeId"] == "west_lane"
    assert "minutes" not in request


@pytest.mark.parametrize("local", [True, False])
def test_existing_network_uses_actual_wgs84_edge(local):
    request, source = preflight.example_request(
        REPO_ROOT / "data/networks/synthetic-preview.json", local=local)
    assert source == "synthetic"
    assert request["center"]["coordType"] == "wgs84ll"
    assert request["originEdgeId"] == "w:154811345:2:0"
    assert request["center"]["lng"] == pytest.approx(121.504429458)
    assert request["center"]["lat"] == pytest.approx(31.331174183)
    assert ("minutes" in request) is (not local)


def test_missing_file_does_not_fall_back(tmp_path):
    with pytest.raises(FileNotFoundError):
        preflight.example_request(tmp_path / "missing.json", local=True)


def test_invalid_network_is_not_replaced(tmp_path):
    original = json.loads((REPO_ROOT /
        "contracts/engine-local-experiment.input.example.json").read_text(encoding="utf-8"))
    original["schemaVersion"] = 999
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="schemaVersion"):
        preflight.example_request(path, local=True)
