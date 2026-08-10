"""
Deployment-config parsing: which .py entry point(s) actually get run in
a container built and shipped from this repo, per Dockerfile,
docker-compose*.yml, and k8s/**/*.yaml (Deploy/k8s included -- it's the
same deployment surface, just split across two directories in this
repo).

This is intentionally narrow: it answers "what does the CMD/command/args
resolve to", not "is this deployment config itself correct or complete".
A compose/k8s file that overrides command with something this parser
doesn't recognize is reported as UNRESOLVED, not silently ignored.
"""

from __future__ import annotations

import ast as pyast
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

import yaml

K8S_WORKLOAD_KINDS = {"Deployment", "CronJob", "Job", "StatefulSet", "DaemonSet", "Pod"}


@dataclass
class DeployedEntry:
    source: str  # e.g. "Dockerfile", "docker-compose.yml:service=iceberg", "k8s/deployment.yaml:iceberg"
    argv: List[str]
    py_file: Optional[str]  # resolved .py argument, relative to repo root, or None if not a python invocation


@dataclass
class DeployReport:
    dockerfile_cmd: Optional[DeployedEntry] = None
    entries: List[DeployedEntry] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    def deployed_py_files(self) -> List[str]:
        seen, out = set(), []
        for e in self.entries:
            if e.py_file and e.py_file not in seen:
                seen.add(e.py_file)
                out.append(e.py_file)
        return out


def _parse_docker_instruction_argv(value: str) -> List[str]:
    value = value.strip()
    if value.startswith("["):
        try:
            return [str(x) for x in pyast.literal_eval(value)]
        except (ValueError, SyntaxError):
            pass
    # shell form: split on whitespace, good enough for this repo's Dockerfiles
    return value.split()


def parse_dockerfile(path: str) -> Optional[DeployedEntry]:
    if not os.path.isfile(path):
        return None
    entrypoint_argv: Optional[List[str]] = None
    cmd_argv: Optional[List[str]] = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        stripped = line.strip()
        # join backslash-continued lines
        while stripped.endswith("\\") and i + 1 < len(lines):
            i += 1
            stripped = stripped[:-1].rstrip() + " " + lines[i].strip()
        if re.match(r"(?i)^ENTRYPOINT\s", stripped):
            entrypoint_argv = _parse_docker_instruction_argv(stripped.split(None, 1)[1])
        elif re.match(r"(?i)^CMD\s", stripped):
            cmd_argv = _parse_docker_instruction_argv(stripped.split(None, 1)[1])
        i += 1

    if entrypoint_argv and cmd_argv:
        argv = entrypoint_argv + cmd_argv
    else:
        argv = entrypoint_argv or cmd_argv or []
    if not argv:
        return None
    py_file = next((a for a in argv if a.endswith(".py")), None)
    return DeployedEntry(source="Dockerfile", argv=argv, py_file=py_file)


def _command_from_compose_value(value) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(v) for v in value]
    return None


def parse_compose_file(path: str, dockerfile_entry: Optional[DeployedEntry], report: DeployReport) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        try:
            doc = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            report.unresolved.append(f"{os.path.basename(path)}: YAML parse error: {exc}")
            return
    if not doc or "services" not in doc:
        return
    for service_name, service in (doc.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        builds_from_repo = "build" in service
        override = _command_from_compose_value(service.get("command"))
        source = f"{os.path.basename(path)}:service={service_name}"
        if override:
            py_file = next((a for a in override if a.endswith(".py")), None)
            report.entries.append(DeployedEntry(source=source, argv=override, py_file=py_file))
        elif builds_from_repo and dockerfile_entry:
            report.entries.append(DeployedEntry(
                source=f"{source} (no command override -- uses {dockerfile_entry.source})",
                argv=dockerfile_entry.argv, py_file=dockerfile_entry.py_file,
            ))
        elif builds_from_repo:
            report.unresolved.append(f"{source}: builds from this repo but no Dockerfile CMD/ENTRYPOINT found")
        # else: external image (e.g. postgres), not part of this codebase -- skip


def parse_k8s_file(path: str, dockerfile_entry: Optional[DeployedEntry], report: DeployReport) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        try:
            docs = list(yaml.safe_load_all(f))
        except yaml.YAMLError as exc:
            report.unresolved.append(f"{os.path.basename(path)}: YAML parse error: {exc}")
            return
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        if kind not in K8S_WORKLOAD_KINDS:
            continue
        pod_spec = _extract_pod_spec(doc, kind)
        if pod_spec is None:
            continue
        for container in pod_spec.get("containers", []) or []:
            name = container.get("name", "?")
            source = f"{os.path.basename(path)}:{kind}/{name}"
            command = container.get("command")
            args = container.get("args")
            if command or args:
                argv = [str(x) for x in (command or [])] + [str(x) for x in (args or [])]
                py_file = next((a for a in argv if a.endswith(".py")), None)
                report.entries.append(DeployedEntry(source=source, argv=argv, py_file=py_file))
            elif dockerfile_entry:
                report.entries.append(DeployedEntry(
                    source=f"{source} (no command/args override -- uses {dockerfile_entry.source})",
                    argv=dockerfile_entry.argv, py_file=dockerfile_entry.py_file,
                ))
            else:
                report.unresolved.append(f"{source}: no command/args and no Dockerfile CMD found")


def _extract_pod_spec(doc: dict, kind: str) -> Optional[dict]:
    spec = doc.get("spec", {})
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    if kind == "Pod":
        return spec
    return (spec.get("template") or {}).get("spec")


def detect_deployed_entry_points(root: str) -> DeployReport:
    report = DeployReport()
    dockerfile_path = os.path.join(root, "Dockerfile")
    if not os.path.isfile(dockerfile_path):
        # Dockerfile commonly lives one directory up from the parsed
        # source root (repo-root/Dockerfile, repo-root/<pkg>/*.py).
        parent = os.path.dirname(root.rstrip(os.sep))
        if os.path.isfile(os.path.join(parent, "Dockerfile")):
            dockerfile_path = os.path.join(parent, "Dockerfile")

    report.dockerfile_cmd = parse_dockerfile(dockerfile_path)
    search_roots = {root, os.path.dirname(dockerfile_path)}

    seen_files = set()
    for base in search_roots:
        for name in ("docker-compose.yml", "docker-compose-prod.yml", "docker-compose.yaml"):
            p = os.path.join(base, name)
            if os.path.isfile(p) and p not in seen_files:
                seen_files.add(p)
                parse_compose_file(p, report.dockerfile_cmd, report)

    for base in search_roots:
        for k8s_dir_name in ("k8s", os.path.join("Deploy", "k8s")):
            k8s_dir = os.path.join(base, k8s_dir_name)
            if not os.path.isdir(k8s_dir):
                continue
            for fn in sorted(os.listdir(k8s_dir)):
                if fn.endswith((".yaml", ".yml")):
                    p = os.path.join(k8s_dir, fn)
                    if p not in seen_files:
                        seen_files.add(p)
                        parse_k8s_file(p, report.dockerfile_cmd, report)

    return report
