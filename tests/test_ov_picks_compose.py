"""The OV picks module's compose service, and the scanner image's copy of the contract.

Ticket ashwinurao/edge-scanner#21. compose.yaml is read with PyYAML (installed with
uvicorn[standard] from requirements.txt), the Dockerfile as text. When the docker CLI
is here, `docker compose config` also reads compose.yaml as compose itself does, with
and without the module's variables. That command reads local files only; nothing here
starts a container or reaches the network.

The rules (issue #21, spec US31-US32 and US39):

- the ov-picks service publishes no port, is off unless its profile is on, and has
  CPU and memory limits;
- every variable it adds has a placeholder default, so an unset one never breaks a
  compose command for the scanner;
- it gets only its Alpaca keys and the SEC contact, never the scanner's whole .env;
- OV's folders are read-only in it; the scanner reads the module's output folder
  read-only;
- the scanner service is otherwise unchanged: its LAN-only port, and ALPACA_STREAM
  left to .env;
- the scanner image copies ov_picks/contract.py, so the route's import works there.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
DOCKERFILE = ROOT / "Dockerfile"

PROFILE = "ov-picks"
OUT_MOUNT = "${OV_PICKS_OUT:-./ov-picks-unused/out}"

# The scanner service as it was before ticket #21, plus the two picks lines it adds.
SCANNER_SERVICE = {
    "build": ".",
    "image": "edge-scanner:local",
    "container_name": "edge-scanner",
    "restart": "unless-stopped",
    "env_file": ".env",
    "environment": {
        "TZ": "${TZ:-America/New_York}",
        "OV_PICKS_DIR": "${OV_PICKS_OUT:+/ov-picks}",               # ticket #21
    },
    "ports": ["${LAN_IP:?set LAN_IP in .env}:${SCANNER_PORT:-8772}:7777"],
    "volumes": [
        "${EDGE_SCANNER_DATA:-./data}:/app/data",
        "${SHARED_SCHWAB_DIR:-./shared-schwab-unused}:/shared-schwab:ro",
        f"{OUT_MOUNT}:/ov-picks:ro",                                 # ticket #21
    ],
}


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


# ---------------------------------------------------------------- variables

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-?+])?[^}]*\}")


def required_variables(text: str) -> set[str]:
    """The variables in ``text`` that compose fails on, or warns about, when unset."""
    return {name for name, op in _VARIABLE.findall(text) if op in ("", "?", ":?")}


def test_only_lan_ip_is_required_so_an_unset_module_variable_breaks_nothing():
    # LAN_IP was required before ticket #21 and still is: the port is published there only.
    assert required_variables(COMPOSE.read_text(encoding="utf-8")) == {"LAN_IP"}


@pytest.mark.parametrize("text, required", [
    ("${A:-x} ${B-x} ${C:+x} ${D+x}", set()),
    ("${A} ${B:?set B} ${C?set C} ${D:-x}", {"A", "B", "C"}),
])
def test_required_variables_reads_compose_interpolation(text, required):
    assert required_variables(text) == required


# ---------------------------------------------------------------- the ov-picks service

def test_ov_picks_publishes_no_port_and_shares_no_host_network(services):
    module = services["ov-picks"]
    assert "ports" not in module and "expose" not in module
    assert module.get("network_mode") not in ("host",)


def test_ov_picks_is_off_unless_its_profile_is_on(services):
    # So the scanner's own `docker compose up -d --build` never needs the module's repo.
    assert services["ov-picks"]["profiles"] == [PROFILE]
    assert "profiles" not in services["edge-scanner"]


def test_ov_picks_builds_the_runtime_image_from_the_modules_clone(services):
    assert services["ov-picks"]["build"] == {"context": "${OV_PICKS_SRC:-../ov-picks}", "target": "runtime"}


def test_ov_picks_has_cpu_and_memory_limits(services):
    module = services["ov-picks"]
    assert module["cpus"] == "${OV_PICKS_CPUS:-2}"
    assert module["mem_limit"] == "${OV_PICKS_MEMORY:-4g}"


def test_ov_picks_gets_only_its_alpaca_keys_and_the_sec_contact(services):
    module = services["ov-picks"]
    assert "env_file" not in module      # no Schwab or other key from .env reaches it
    assert module["environment"] == {
        "ALPACA_DATA_API_KEY": "${ALPACA_API_KEY:-}",
        "ALPACA_DATA_SECRET_KEY": "${ALPACA_SECRET_KEY:-}",
        "SEC_USER_AGENT": "${SEC_USER_AGENT:-}",
    }


def _mounts(service):
    found = {}
    for volume in service["volumes"]:
        parts = volume.rsplit(":", 2) if volume.endswith((":ro", ":rw")) else volume.rsplit(":", 1) + ["rw"]
        source, target, mode = parts
        found[target] = (source, mode)
    return found


def test_ov_picks_writes_its_own_folders_and_reads_ovs_folders_read_only(services):
    assert _mounts(services["ov-picks"]) == {
        "/data": ("${OV_PICKS_DATA:-./ov-picks-unused/data}", "rw"),
        "/out": (OUT_MOUNT, "rw"),
        "/ov/market": ("${OV_MARKET_DIR:-./ov-picks-unused/ov-market}", "ro"),
        "/ov/outputs": ("${OV_OUTPUTS_DIR:-./ov-picks-unused/ov-outputs}", "ro"),
    }


def test_mount_modes_are_read_from_the_volume_text():
    assert _mounts({"volumes": ["./a:/a", "./b:/b:ro", "./c:/c:rw"]}) == {
        "/a": ("./a", "rw"), "/b": ("./b", "ro"), "/c": ("./c", "rw")}


# ---------------------------------------------------------------- the scanner service

def test_the_scanner_service_is_unchanged_apart_from_the_picks_folder(services):
    assert services["edge-scanner"] == SCANNER_SERVICE


def test_the_scanner_reads_the_modules_output_folder_read_only(services):
    scanner, module = _mounts(services["edge-scanner"]), _mounts(services["ov-picks"])
    assert scanner["/ov-picks"] == (module["/out"][0], "ro")


def test_alpaca_stream_stays_in_env_for_the_scanner(services):
    # ALPACA_STREAM=0 on the server comes from .env (fork PR #3); compose never overrides it.
    assert "ALPACA_STREAM" not in services["edge-scanner"]["environment"]
    assert "ALPACA_STREAM" not in COMPOSE.read_text(encoding="utf-8").split("services:", 1)[1]


# ---------------------------------------------------------------- docker compose config

MODULE_ENV = {
    "COMPOSE_PROFILES": PROFILE,
    "OV_PICKS_SRC": "/srv/example/ov-picks-src",
    "OV_PICKS_DATA": "/srv/example/ov-picks",
    "OV_PICKS_OUT": "/srv/example/ov-picks-out",
    "OV_MARKET_DIR": "/srv/example/ov-market",
    "OV_OUTPUTS_DIR": "/srv/example/ov-outputs",
    "SEC_USER_AGENT": "Example contact@example.com",
    "OV_PICKS_CPUS": "1.5",
    "OV_PICKS_MEMORY": "3g",
}
SCANNER_ENV = {"LAN_IP": "192.0.2.10", "ALPACA_API_KEY": "fake-key", "ALPACA_SECRET_KEY": "fake-secret",
               "ALPACA_STREAM": "0"}


def _compose_config(tmp_path, env_lines: dict, *args):
    docker = shutil.which("docker")
    if docker is None or subprocess.run([docker, "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("the docker compose CLI is not installed here")
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in env_lines.items()), encoding="utf-8")
    # Only what the CLI needs: no OV_PICKS_* or key from this shell may leak into the check.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_CONFIG")}
    result = subprocess.run(
        [docker, "compose", "--project-directory", str(tmp_path), "-f", str(COMPOSE), *args,
         "config", "--format", "json"],
        capture_output=True, text=True, env=env, timeout=60)
    return result


@pytest.mark.parametrize("with_module", [False, True], ids=["without-module-vars", "with-module-vars"])
@pytest.mark.parametrize("profile", [[], ["--profile", PROFILE]], ids=["profile-off", "profile-on"])
def test_docker_compose_config_is_valid_with_and_without_the_module_variables(tmp_path, with_module, profile):
    env = {**SCANNER_ENV, **(MODULE_ENV if with_module else {})}
    result = _compose_config(tmp_path, env, *profile)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    scanner = config["services"]["edge-scanner"]
    assert [(p["host_ip"], p["published"], p["target"]) for p in scanner["ports"]] == [("192.0.2.10", "8772", 7777)]
    assert scanner["environment"]["OV_PICKS_DIR"] == ("/ov-picks" if with_module else "")
    picks_mount = [v for v in scanner["volumes"] if v["target"] == "/ov-picks"]
    assert len(picks_mount) == 1 and picks_mount[0]["read_only"] is True
    module_on = with_module or bool(profile)    # MODULE_ENV turns the profile on itself
    assert ("ov-picks" in config["services"]) == module_on
    if module_on:
        module = config["services"]["ov-picks"]
        assert "ports" not in module and "expose" not in module
        assert module["environment"]["ALPACA_DATA_API_KEY"] == "fake-key"
        read_only = {v["target"]: bool(v.get("read_only")) for v in module["volumes"]}
        assert read_only == {"/data": False, "/out": False, "/ov/market": True, "/ov/outputs": True}


def test_docker_compose_config_still_says_when_lan_ip_is_missing(tmp_path):
    # The scanner's one required variable keeps failing loudly, as before ticket #21.
    result = _compose_config(tmp_path, {"ALPACA_API_KEY": "fake-key"})
    assert result.returncode != 0 and "set LAN_IP in .env" in result.stderr


# ---------------------------------------------------------------- the scanner image's files

def _instructions(text: str) -> list[tuple[str, str]]:
    found, current = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or (not current and not line):
            continue
        current += " " + line
        if current.endswith("\\"):
            current = current[:-1]
            continue
        word, _, rest = current.strip().partition(" ")
        found.append((word.upper(), rest.strip()))
        current = ""
    return found


def stage_image_files(dockerfile_text: str, app: Path) -> None:
    """Copy into ``app`` what the Dockerfile's final stage copies from the build context."""
    steps = _instructions(dockerfile_text)
    last_from = max(i for i, (word, _) in enumerate(steps) if word == "FROM")
    final = steps[last_from + 1:]
    assert ("WORKDIR", "/app") in final
    for word, rest in final:
        if word != "COPY":
            continue
        args = shlex.split(rest)
        if any(a.startswith("--from") for a in args):
            continue        # from another stage (the built dashboard), not the context
        args = [a for a in args if not a.startswith("--")]
        sources, dest = args[:-1], args[-1]
        target = app if dest in (".", "./") else app / dest
        for source in sources:
            path = ROOT / source
            if path.is_dir():
                shutil.copytree(path, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            elif dest.endswith("/") or len(sources) > 1 or dest in (".", "./"):
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / path.name)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


# The route's own import, the way scripts/run_live.py sets the path: the app folder first.
_ROUTE_IMPORT = """
import sys
from pathlib import Path
app = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(app))
from scanner import ov_picks_route
contract = ov_picks_route._load_contract()
print("none" if contract is None else Path(contract.__file__).resolve().relative_to(app).as_posix())
"""


def _route_contract_in(app: Path) -> str:
    # -I: neither this checkout nor PYTHONPATH is on the path, only the staged files.
    result = subprocess.run([sys.executable, "-I", "-c", _ROUTE_IMPORT, str(app)],
                            cwd=app, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip().splitlines()[-1]


def test_the_route_imports_the_contract_from_what_the_image_copies(tmp_path):
    stage_image_files(DOCKERFILE.read_text(encoding="utf-8"), tmp_path)
    assert _route_contract_in(tmp_path) == "ov_picks/contract.py"


def test_the_image_copies_only_the_contract_from_ov_picks(tmp_path):
    stage_image_files(DOCKERFILE.read_text(encoding="utf-8"), tmp_path)
    assert sorted(p.name for p in (tmp_path / "ov_picks").rglob("*") if p.is_file()) == ["__init__.py", "contract.py"]


def test_without_the_contract_copy_the_route_finds_no_reader(tmp_path):
    # The Dockerfile before ticket #21 copied scanner/ and scripts/ only.
    text = "\n".join(line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
                     if not line.startswith("COPY ov_picks"))
    stage_image_files(text, tmp_path)
    assert not (tmp_path / "ov_picks").exists()
    assert _route_contract_in(tmp_path) == "none"
