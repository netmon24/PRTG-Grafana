"""Auslieferung: Compose zieht fertige Images, GitHub Actions baut und veröffentlicht sie."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_SERVICES = ("collector", "portal", "setup")
IMAGE = "ghcr.io/netmon24/prtg-grafana"


def _yaml(path: str):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def _version() -> str:
    data = tomllib.loads((ROOT / "collector/pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_compose_only_pulls_images_so_portainer_and_servers_need_no_source():
    for name, service in _yaml("docker-compose.yml")["services"].items():
        assert "build" not in service, f"{name} baut aus dem Quelltext"


def test_app_services_use_the_versioned_ghcr_image():
    services = _yaml("docker-compose.yml")["services"]
    for name in APP_SERVICES:
        assert services[name]["image"].startswith(f"{IMAGE}:${{PRTG_GRAFANA_VERSION:-"), name


def test_default_image_version_matches_the_package_version():
    image = _yaml("docker-compose.yml")["services"]["collector"]["image"]
    assert image == f"{IMAGE}:${{PRTG_GRAFANA_VERSION:-{_version()}}}"


def test_dev_override_builds_the_app_image_from_source():
    services = _yaml("docker-compose.dev.yml")["services"]
    for name in APP_SERVICES:
        assert services[name]["build"]["dockerfile"] == "collector/Dockerfile", name
        assert services[name]["build"]["context"] == ".", name


def test_env_example_documents_the_version_variable():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^#?\s*PRTG_GRAFANA_VERSION=", text, re.MULTILINE)


def test_dockerfile_links_the_package_to_the_repository():
    text = (ROOT / "collector/Dockerfile").read_text(encoding="utf-8")
    assert "org.opencontainers.image.source=https://github.com/netmon24/PRTG-Grafana" in text


def _dockerfile() -> str:
    return (ROOT / "collector/Dockerfile").read_text(encoding="utf-8")


def test_image_installs_the_locked_dependencies_with_hashes():
    """Das Image muss genau die Versionen aus uv.lock enthalten, die CI getestet hat."""
    text = _dockerfile()
    assert "uv.lock" in text
    assert "uv export --frozen" in text
    assert "--require-hashes" in text
    # kein freies Aufloesen aus pyproject.toml (nur ">=") beim Bau
    for m in re.finditer(r"pip install((?:[ \t]+[^\s&]+)*?)[ \t]+\.(?:\s|$)", text):
        assert "--no-deps" in m.group(1), m.group(0)


def test_image_build_tools_and_base_images_are_pinned():
    text = _dockerfile()
    for line in (ln for ln in text.splitlines() if ln.startswith("FROM ")):
        assert re.search(r"@sha256:[0-9a-f]{64}", line), line
    assert re.search(r"COPY --from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", text)
    assert "--no-build-isolation" in text  # Build-Backend kommt aus dem Lock, nicht frei aus PyPI


def test_build_backend_is_part_of_the_lock():
    pyproject = tomllib.loads((ROOT / "collector/pyproject.toml").read_text(encoding="utf-8"))
    backend = pyproject["build-system"]["requires"]
    assert all("==" in r for r in backend), backend
    lock = (ROOT / "collector/uv.lock").read_text(encoding="utf-8")
    assert 'name = "hatchling"' in lock


def test_ci_runs_lint_types_and_tests_on_push_and_pull_request():
    workflow = _yaml(".github/workflows/ci.yml")
    triggers = workflow[True]  # YAML liest `on` als Wahrheitswert
    assert "push" in triggers and "pull_request" in triggers
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for command in ("ruff check", "ruff format --check", "mypy", "pytest"):
        assert command in text, command


def test_release_builds_multi_arch_on_version_tags_only():
    path = ROOT / ".github/workflows/release.yml"
    workflow = _yaml(".github/workflows/release.yml")
    assert workflow[True]["push"]["tags"] == ["v*.*.*"]
    text = path.read_text(encoding="utf-8")
    assert "linux/amd64,linux/arm64" in text
    assert "ghcr.io/netmon24/prtg-grafana" in text


def test_release_smoke_tests_the_built_image_before_pushing():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    smoke = text.index("pip check")
    assert text.index("load: true") < smoke < text.index("push: true")


def test_release_refuses_a_tag_that_differs_from_the_package_version():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "pyproject.toml" in text and "GITHUB_REF_NAME" in text


def test_release_runs_the_checks_before_publishing():
    jobs = _yaml(".github/workflows/release.yml")["jobs"]
    assert "needs" in jobs["image"] and "checks" in jobs["image"]["needs"]


def test_handbook_explains_every_required_variable_for_portainer():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
    section = (ROOT / "docs/ausrollen.md").read_text(encoding="utf-8")
    assert required, "keine Pflichtvariablen gefunden"
    for name in sorted(required):
        assert name in section, f"{name} fehlt in docs/ausrollen.md"
    assert "Portainer" in section and "docker login" in section


def test_every_workflow_action_is_pinned_to_a_full_commit_sha():
    paths = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
    assert len(paths) >= 2, "Workflows nicht gefunden"
    uses = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*-?\s*uses:\s*(\S+)", line)
            if match:
                uses += 1
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", match.group(1)), (
                    f"{path.name}: {match.group(1)}"
                )
    assert uses >= 8, "zu wenige Actions gefunden, die Suche greift nicht"


def test_only_the_image_job_may_write_packages():
    workflow = _yaml(".github/workflows/release.yml")
    assert "packages" not in workflow.get("permissions", {})
    assert workflow["jobs"]["checks"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["image"]["permissions"] == {"contents": "read", "packages": "write"}


def test_dependabot_keeps_the_pinned_actions_current():
    config = _yaml(".github/dependabot.yml")
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert {"github-actions", "docker", "uv"} <= ecosystems


def _env_example_lines() -> list[str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_env_example_has_no_inline_comments_that_compose_would_take_as_values():
    for line in _env_example_lines():
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=\S*", line), (
            f"Kommentar oder Leerraum im Wert: {line!r}"
        )


def test_env_example_leaves_optional_overrides_commented_out():
    active = {ln.split("=", 1)[0] for ln in _env_example_lines()}
    for name in (
        "INFLUX_INTERNAL_URL",
        "GRAFANA_INTERNAL_URL",
        "INTERNAL_TLS_VERIFY",
        "ACME_DIRECTORY_URL",
    ):
        assert name not in active, f"{name} darf nicht leer aktiv sein"


def test_env_example_declares_every_required_variable_empty():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
    declared = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in _env_example_lines()}
    for name in required:
        assert declared.get(name) == "", f"{name} fehlt oder ist vorbelegt"


def test_dockerignore_keeps_secrets_and_compiled_files_out_of_the_build_context():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "*" in lines  # alles ausschließen, nur Benötigtes freigeben
    assert "**/__pycache__" in lines
    copied = re.findall(r"^COPY (?!--from)(\S+)", _dockerfile(), re.MULTILINE)
    for src in copied:
        assert any(f"!{src.rstrip('/')}" == ln for ln in lines), src


def test_release_workflow_publishes_only_commits_that_are_on_main():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "merge-base --is-ancestor" in text and "origin/main" in text


def test_release_scans_the_image_with_trivy_before_publishing():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert text.index("aquasecurity/trivy-action") < text.index("push: true")
    assert 'exit-code: "1"' in text and "ignore-unfixed: true" in text


def test_weekly_scan_covers_every_image_of_the_compose_file():
    compose = _yaml("docker-compose.yml")
    images = {
        s["image"].split(":${")[0].split(":")[0] if "${" in s["image"] else s["image"]
        for s in compose["services"].values()
        if "image" in s
    }
    text = (ROOT / ".github/workflows/sicherheit.yml").read_text(encoding="utf-8")
    for image in images:
        assert image.split(":")[0] in text, image
    assert "grafana/grafana-oss:" + _grafana_tag(compose) in text


def _grafana_tag(compose) -> str:
    return compose["services"]["grafana"]["image"].split(":")[1]
