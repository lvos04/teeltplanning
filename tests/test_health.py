import re
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

import teeltplanning
from teeltplanning.main import app

ROOT = Path(__file__).resolve().parent.parent


def test_health_geeft_status_en_versie():
    with TestClient(app) as client:
        antwoord = client.get("/health")
    assert antwoord.status_code == 200
    assert antwoord.json() == {"status": "ok", "versie": teeltplanning.__version__}


def test_versie_heeft_een_bron():
    assert re.fullmatch(r"\d+\.\d+\.\d+", teeltplanning.__version__)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "teeltplanning.__version__"
    for workflow in ("image.yml", "auto-release.yml"):
        assert "teeltplanning/__init__.py" in (ROOT / ".github" / "workflows" / workflow).read_text()


def test_testbuild_zet_nooit_latest():
    image = (ROOT / ".github" / "workflows" / "image.yml").read_text()
    assert "branches: ['ccr-*', 'fase-*']" in image
    assert "latest=${{ startsWith(github.ref, 'refs/tags/') && 'auto' || 'false' }}" in image
    assert "-rc,enable=${{ !startsWith(github.ref, 'refs/tags/') }}" in image


def test_dockerfile_gebruiker_568_healthcheck_en_libreoffice():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER 568:568" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile
    assert "libreoffice-calc-nogui" in dockerfile
    assert '--workers", "1", "--no-proxy-headers"' in dockerfile
