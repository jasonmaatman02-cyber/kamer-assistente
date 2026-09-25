"""De CI-workflow moet geldige YAML zijn: een ongeldige workflow start GEEN job (run 'failure' met 0 jobs) en
valt dus alleen op als je zelf naar GitHub kijkt."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_is_valid_yaml_with_the_expected_steps():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = data["jobs"]["test"]
    names = [str(s.get("name") or s.get("uses")) for s in job["steps"]]
    assert any(n.startswith("Lint") for n in names) and "Tests" in names
    assert job["env"]["TZ"] not in ("UTC", "Europe/Amsterdam")      # tests mogen niet van de machinezone afhangen
    assert data.get("on") or data.get(True)                          # 'on' wordt door YAML 1.1 soms als True gelezen
