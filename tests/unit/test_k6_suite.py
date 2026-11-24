"""Contract and static hygiene tests for the TASK-24 k6 performance suite.

Satisfies: RNF-06
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.domain.access import Permission
from app.main import app

pytestmark = [pytest.mark.unit, pytest.mark.requirement("RNF-06")]

REPO_ROOT = Path(__file__).resolve().parents[2]
K6_ROOT = REPO_ROOT / "tests" / "k6"
SCENARIOS = (
    "smoke.js",
    "load.js",
    "stress.js",
    "spike.js",
    "soak.js",
    "websocket-fanout.js",
    "stock-race.js",
)


def _all_k6_js_files() -> list[Path]:
    return sorted(K6_ROOT.rglob("*.js"))


def test_k6_suite_structure_exists() -> None:
    expected = [
        K6_ROOT / ".gitignore",
        K6_ROOT / "Dockerfile",
        K6_ROOT / "README.md",
        K6_ROOT / "out" / ".gitkeep",
        K6_ROOT / "config" / "env.js",
        K6_ROOT / "config" / "options.js",
        K6_ROOT / "lib" / "auth.js",
        K6_ROOT / "lib" / "http.js",
        K6_ROOT / "lib" / "metrics.js",
        K6_ROOT / "lib" / "setup.js",
        K6_ROOT / "lib" / "summary.js",
        K6_ROOT / "lib" / "workloads.js",
        K6_ROOT / "lib" / "ws.js",
        REPO_ROOT / "docs" / "k6_breakpoint_report.md",
        *[K6_ROOT / "scenarios" / name for name in SCENARIOS],
    ]
    missing = [str(path.relative_to(REPO_ROOT)) for path in expected if not path.exists()]
    assert not missing, f"Missing k6 suite files: {missing}"


def test_k6_js_follows_security_and_sonar_rules() -> None:
    forbidden_patterns = (
        ("Math.random(", "Use deterministic PRNG helper instead of Math.random (S2245)"),
        ("'k6/ws'", "Use modern 'k6/websockets' module instead of legacy 'k6/ws'"),
        ('"k6/ws"', "Use modern 'k6/websockets' module instead of legacy 'k6/ws'"),
        ("eyJ", "Do not commit hardcoded JWT literals (Gitleaks)"),
        ("jslib.k6.io", "Keep summary generation self-contained without external CDN imports"),
        (".replace(/", "Prefer String.prototype.replaceAll over global regex replace (S7781)"),
    )
    for js_file in _all_k6_js_files():
        content = js_file.read_text(encoding="utf-8")
        for needle, reason in forbidden_patterns:
            assert needle not in content, f"{js_file.name}: {reason}"


def test_k6_auth_permissions_match_pbac_enum() -> None:
    auth_js = (K6_ROOT / "lib" / "auth.js").read_text(encoding="utf-8")
    tokens = set(re.findall(r"'(?:orders|kitchen):[a-z_]+:[a-z_]+'", auth_js))
    declared = {token.strip("'") for token in tokens}
    valid_permissions = {perm.value for perm in Permission}
    assert declared, "Expected PBAC permission strings in tests/k6/lib/auth.js"
    assert declared <= valid_permissions


def _collect_mounted_paths(routes: list[object], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(f"{prefix}{path}")
            continue
        include_ctx = getattr(route, "include_context", None)
        orig_router = getattr(route, "original_router", None)
        if include_ctx is not None and orig_router is not None:
            nested_prefix = f"{prefix}{getattr(include_ctx, 'prefix', '')}"
            paths |= _collect_mounted_paths(list(orig_router.routes), nested_prefix)
    return paths


def test_k6_catalog_and_routes_align_with_backend() -> None:
    setup_js = (K6_ROOT / "lib" / "setup.js").read_text(encoding="utf-8")
    for plate_id in ("PLATE-BURGER-01", "PLATE-QUES-01", "PLATE-CESAR-01", "PLATE-SIM-0001"):
        assert plate_id in setup_js

    mounted_paths = _collect_mounted_paths(list(app.routes))
    required_paths = {
        "/api/v1/orders/health",
        "/api/v1/kitchen/health",
        "/api/v1/orders/orders",
        "/api/v1/orders/orders/{orderId}",
        "/api/v1/orders/orders/{orderId}/items",
        "/api/v1/orders/orders/{orderId}/confirm",
        "/api/v1/kitchen/kds/queue",
        "/api/v1/kitchen/orders/{orderId}/start-preparation",
        "/api/v1/kitchen/orders/{orderId}/items/{itemId}/ready",
        "/api/v1/orders/ws/waiter",
        "/api/v1/kitchen/ws/kds",
    }
    assert required_paths <= mounted_paths


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_k6_inspect_scenarios_succeeds(scenario: str) -> None:
    k6_bin = shutil.which("k6")
    if k6_bin is None:
        pytest.skip("k6 binary is not installed in PATH")

    scenario_path = K6_ROOT / "scenarios" / scenario
    completed = subprocess.run(  # noqa: S603
        [k6_bin, "inspect", str(scenario_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    inspected = json.loads(completed.stdout)
    assert inspected.get("scenarios"), f"No scenarios configured in {scenario}"


def test_k6_dockerfile_and_ci_security_hygiene() -> None:
    dockerfile = (K6_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert ":latest" not in dockerfile
    assert re.search(r"^FROM\s+grafana/k6:\d+\.\d+\.\d+", dockerfile, re.MULTILINE)
    assert re.search(r"^USER\s+(?!root\b)\S+", dockerfile, re.MULTILINE)

    ci_yml = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    k6_job_match = re.search(
        r"(?ms)^  k6-smoke:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        ci_yml,
    )
    assert k6_job_match is not None, "Expected k6-smoke job in .github/workflows/ci.yml"
    k6_job = k6_job_match.group(1)
    assert "POSTGRES_PASSWORD" not in k6_job
    assert "POSTGRES_HOST_AUTH_METHOD: trust" in k6_job
    assert "--only-binary :all:" in k6_job
    assert "--require-hashes" in k6_job
    for lock_path in (
        REPO_ROOT / "requirements" / "runtime.lock.txt",
        REPO_ROOT / "requirements" / "dev.lock.txt",
        REPO_ROOT / "tests" / "api" / "postman" / "package.json",
        REPO_ROOT / "tests" / "api" / "postman" / "package-lock.json",
    ):
        assert lock_path.exists(), f"Missing CI lockfile: {lock_path.relative_to(REPO_ROOT)}"
