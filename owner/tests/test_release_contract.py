import shutil
import subprocess
import os
import hashlib
import json
import pytest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "prepare-release-runtime.sh"


@pytest.mark.skipif(shutil.which("node") is None, reason="runtime Node is required for external frontend attestation")
@pytest.mark.parametrize("tamper", [None, "asset", "source", "checker"])
def test_external_frontend_requires_pinned_source_checker_and_exact_build(tmp_path, tamper):
    root = _release_fixture(tmp_path)
    revision = "a" * 40
    checker = (
        "import{readFileSync}from'node:fs';import{createHash}from'node:crypto';"
        "console.log(createHash('sha256').update(readFileSync(process.argv[process.argv.indexOf('--dist')+1]+'/assets/index-test.js')).digest('hex'));"
    )
    (root / "frontend.json").write_text(json.dumps({"revision": revision, "checker_sha256": hashlib.sha256(checker.encode()).hexdigest()}))
    (root / "frontend-check.mjs").write_text(checker)
    (root / "FRONTEND_SOURCE_SHA").write_text(revision + "\n")
    asset = root.parent / "web/dist/assets/index-test.js"
    (root / "FRONTEND_ASSETS.txt").write_text(hashlib.sha256(asset.read_bytes()).hexdigest() + "\n")
    if tamper == "asset":
        asset.write_text("console.log('different build')\n")
    elif tamper == "source":
        (root / "FRONTEND_SOURCE_SHA").write_text("b" * 40)
    elif tamper == "checker":
        (root / "frontend-check.mjs").write_text("console.log('untrusted checker')")
    result = _check(root)
    assert (result.returncode == 0) is (tamper is None), result.stderr


def test_silverbullet_service_uses_host_data_root() -> None:
    unit = SCRIPT.with_name("controlcenter-plancia-silverbullet.service").read_text()
    assert "ExecStart=%h/.local/share/controlcenter/plancia-silverbullet-2.12.1/silverbullet" in unit
    assert "ControlCenter/_local" not in unit


def test_release_preparer_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_release_preparer_is_tracked_executable() -> None:
    root = SCRIPT.parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "-s", "deploy/prepare-release-runtime.sh"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    assert tracked and tracked[0] == "100755"


def _release_fixture(tmp_path: Path, *, with_index: bool = True, with_asset: bool = True) -> Path:
    root = tmp_path / "release" / "owner"
    deploy = root / "deploy"
    assets = root.parent / "web" / "dist" / "assets"
    deploy.mkdir(parents=True)
    assets.mkdir(parents=True)
    shutil.copy2(SCRIPT, deploy / SCRIPT.name)
    (root / "requirements.txt").write_text("fastapi==0.0\n", encoding="utf-8")
    if with_index:
        (root.parent / "web" / "dist" / "index.html").write_text(
            '<script type="module" src="/assets/index-test.js"></script>\n',
            encoding="utf-8",
        )
    if with_asset:
        (assets / "index-test.js").write_text("console.log('ok')\n", encoding="utf-8")
    return root


def _check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / "deploy" / SCRIPT.name), "--check"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_preflight_rejects_missing_web_index(tmp_path: Path) -> None:
    result = _check(_release_fixture(tmp_path, with_index=False))
    assert result.returncode == 67
    assert "missing built web bundle" in result.stderr


def test_release_preflight_rejects_missing_javascript_asset(tmp_path: Path) -> None:
    result = _check(_release_fixture(tmp_path, with_asset=False))
    assert result.returncode == 67
    assert "references missing asset" in result.stderr


def test_release_preflight_rejects_unbuilt_development_index(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    (root.parent / "web" / "dist" / "index.html").write_text(
        '<script type="module" src="/src/main.tsx"></script>\n',
        encoding="utf-8",
    )
    result = _check(root)
    assert result.returncode == 67
    assert "unbuilt development index" in result.stderr


def test_release_preflight_rejects_missing_referenced_stylesheet(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    (root.parent / "web" / "dist" / "index.html").write_text(
        (
            '<script type="module" src="/assets/index-test.js"></script>\n'
            '<link rel="stylesheet" href="/assets/index-test.css">\n'
        ),
        encoding="utf-8",
    )
    result = _check(root)
    assert result.returncode == 67
    assert "references missing asset" in result.stderr
    assert "index-test.css" in result.stderr


def test_release_preflight_rejects_index_referencing_wrong_asset(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    (root.parent / "web" / "dist" / "index.html").write_text(
        '<script type="module" src="/assets/missing.js"></script>\n',
        encoding="utf-8",
    )
    result = _check(root)
    assert result.returncode == 67
    assert "references missing asset" in result.stderr


def test_release_preflight_accepts_built_web_bundle(tmp_path: Path) -> None:
    result = _check(_release_fixture(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "release_preflight=ok" in result.stdout
