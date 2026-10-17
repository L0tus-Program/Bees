"""Consolida metadados e contagens de CI sem exportar logs ou conteúdo de testes.

Não executa testes, não altera workflow, não aprova release. Exige quatro jobs
concluídos no SHA informado, logs recuperáveis e resumos reconhecidos. Qualquer
falha de consulta, contrato, job, log ou exportação termina com status diferente
de zero; nenhum relatório parcial recebe status de sucesso.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

JOBS = {
    "API / ubuntu-latest": ("Testar no Linux",),
    "API / windows-latest": (
        "Validar guarda nativa de arquivos no Windows",
        "Testar no Windows com dono padrão da conta",
    ),
    "Interface": ("Run npm run test",),
    "Distribuição Docker / Linux": ("Run docker run --rm --network none bees-verification:local",),
}
REQUIRED_STEPS = {
    "API / ubuntu-latest": (
        "Run uv sync --locked",
        "Run uv run --locked ruff check .",
        "Run uv run --locked ruff format --check .",
        *JOBS["API / ubuntu-latest"],
    ),
    "API / windows-latest": (
        "Run uv sync --locked",
        "Run uv run --locked ruff check .",
        "Run uv run --locked ruff format --check .",
        *JOBS["API / windows-latest"],
    ),
    "Interface": ("Run npm ci", "Run npm run lint", "Run npm run build", *JOBS["Interface"]),
    "Distribuição Docker / Linux": (
        "Run uv sync --locked",
        "Run docker compose config --quiet",
        "Run docker compose build",
        "Run .venv/bin/python scripts/check-container.py",
        "Run docker build --target verification -t bees-verification:local .",
        *JOBS["Distribuição Docker / Linux"],
    ),
}
_FIELDS = "databaseId,headSha,conclusion,status,url,createdAt,updatedAt,jobs"
MAX_LOG_BYTES = 32 * 1024 * 1024


class ReportError(RuntimeError):
    """Código fechado; nunca inclui stderr, logs ou URLs de download assinadas."""


def _reject(code):
    raise ReportError(code)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            _reject("ci_metadata_invalid")
        result[key] = value
    return result


def _exact(value, fields):
    return isinstance(value, dict) and set(value) == set(fields)


def _positive(value):
    return type(value) is int and value > 0


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _gh(arguments, failure):
    try:
        result = subprocess.run(
            ["gh", *arguments], capture_output=True, text=True, encoding="utf-8", timeout=120
        )
    except OSError, subprocess.SubprocessError, UnicodeError:
        _reject(failure)
    if result.returncode != 0 or not result.stdout.strip():
        _reject(failure)
    return result.stdout


def _metadata(raw, repository, run_id, expected_sha):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except ValueError, RecursionError:
        _reject("ci_metadata_invalid")
    if not _exact(value, _FIELDS.split(",")):
        _reject("ci_metadata_invalid")
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if (
        value["databaseId"] != run_id
        or type(value["databaseId"]) is not int
        or value["headSha"] != expected_sha
        or value["url"] != url
        or not _timestamp(value["createdAt"])
        or not _timestamp(value["updatedAt"])
        or value["updatedAt"] < value["createdAt"]
    ):
        _reject("ci_run_binding_invalid")
    if value["status"] != "completed" or value["conclusion"] != "success":
        _reject("ci_run_not_successful")
    jobs = value["jobs"]
    if not isinstance(jobs, list) or len(jobs) != len(JOBS):
        _reject("ci_jobs_incomplete")
    names, identities = set(), set()
    for job in jobs:
        if not _exact(
            job,
            [
                "completedAt",
                "conclusion",
                "databaseId",
                "name",
                "startedAt",
                "status",
                "steps",
                "url",
            ],
        ):
            _reject("ci_job_invalid")
        name, identity = job["name"], job["databaseId"]
        if (
            not isinstance(name, str)
            or name not in JOBS
            or name in names
            or not _positive(identity)
            or identity in identities
        ):
            _reject("ci_job_invalid")
        names.add(name)
        identities.add(identity)
        if job["status"] != "completed" or job["conclusion"] != "success":
            _reject("ci_job_not_successful")
        if (
            job["url"] != f"{url}/job/{identity}"
            or not _timestamp(job["startedAt"])
            or not _timestamp(job["completedAt"])
            or job["completedAt"] < job["startedAt"]
        ):
            _reject("ci_job_invalid")
        steps = job["steps"]
        if not isinstance(steps, list) or not steps:
            _reject("ci_steps_incomplete")
        numbers, step_names = set(), set()
        for step in steps:
            if not _exact(step, ["conclusion", "name", "number", "status"]):
                _reject("ci_step_invalid")
            if (
                not isinstance(step["name"], str)
                or not step["name"]
                or len(step["name"]) > 250
                or not _positive(step["number"])
                or step["number"] in numbers
                or step["name"] in step_names
                or step["status"] != "completed"
            ):
                _reject("ci_step_invalid")
            numbers.add(step["number"])
            step_names.add(step["name"])
            skipped_platform = (
                name == "API / ubuntu-latest"
                and step["name"]
                in (*JOBS["API / windows-latest"], "Validar corpus de pesquisa no Windows")
                or name == "API / windows-latest"
                and step["name"] == "Testar no Linux"
            )
            if step["conclusion"] != "success" and not (
                skipped_platform and step["conclusion"] == "skipped"
            ):
                _reject("ci_step_not_successful")
        if not set(REQUIRED_STEPS[name]).issubset(step_names):
            _reject("ci_steps_incomplete")
    if names != set(JOBS):
        _reject("ci_jobs_incomplete")
    return value


def _counts(log, job):
    data = log.encode("utf-8")
    if not data or len(data) > MAX_LOG_BYTES:
        _reject("ci_log_invalid")
    found = {name: [] for name in JOBS[job["name"]]}
    # gh emits job<TAB>step<TAB>timestamp message. No arbitrary log text is exported.
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[0] != job["name"] or parts[1] not in found:
            continue
        message = re.sub(r"\x1b\[[0-9;]*m", "", parts[2])
        if job["name"] == "Interface":
            match = re.search(r"\bTests\s+(\d+) passed \((\d+)\)\s*$", message)
            if match:
                if match[1] != match[2] or int(match[1]) <= 0:
                    _reject("ci_summary_invalid")
                found[parts[1]].append({"passed": int(match[1]), "skipped": 0})
        else:
            match = re.search(
                r"(?P<counts>\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected)"
                r"(?:, \d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected))*)"
                r" in \d+(?:\.\d+)?s(?: \(\d+:\d+(?::\d+)?\))?\s*=*\s*$",
                message,
            )
            if match:
                pairs = re.findall(r"(\d+) ([a-z]+)", match["counts"])
                counts = {kind: int(number) for number, kind in pairs}
                if (
                    len(counts) != len(pairs)
                    or counts.get("passed", 0) <= 0
                    or any(counts.get(key, 0) for key in ("failed", "error", "errors", "xpassed"))
                ):
                    _reject("ci_summary_invalid")
                found[parts[1]].append(
                    {
                        "passed": counts["passed"],
                        "skipped": counts.get("skipped", 0),
                        "deselected": counts.get("deselected", 0),
                        "xfailed": counts.get("xfailed", 0),
                    }
                )
    if any(len(items) != 1 for items in found.values()):
        _reject("ci_log_summary_missing_or_ambiguous")
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "summaries": [{"step": name, **items[0]} for name, items in found.items()],
    }


def collect(repository, run_id, expected_sha):
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository
    ):
        _reject("ci_arguments_invalid")
    if (
        not _positive(run_id)
        or not isinstance(expected_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", expected_sha)
    ):
        _reject("ci_arguments_invalid")
    raw = _gh(
        ["run", "view", str(run_id), "--repo", repository, "--json", _FIELDS],
        "ci_metadata_fetch_failed",
    )
    run = _metadata(raw, repository, run_id, expected_sha)
    jobs = []
    for job in sorted(run["jobs"], key=lambda value: value["name"]):
        log = _gh(
            [
                "run",
                "view",
                str(run_id),
                "--repo",
                repository,
                "--job",
                str(job["databaseId"]),
                "--log",
            ],
            "ci_log_fetch_failed",
        )
        observation = _counts(log, job)
        jobs.append(
            {
                "id": job["databaseId"],
                "name": job["name"],
                "url": job["url"],
                "conclusion": "success",
                "completed_at": job["completedAt"],
                "log": observation,
            }
        )
    return {
        "format": "bees.ci-report/v1",
        "status": "verified",
        "repository": repository,
        "run_id": run_id,
        "head_sha": expected_sha,
        "url": run["url"],
        "run_updated_at": run["updatedAt"],
        "jobs": jobs,
        "counting": "Separate observations per job/step; never sum overlapping suites.",
    }


def export(report, path):
    """Publica arquivo completo sem sobrescrever evidência anterior; nenhum log bruto."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".bees-ci-", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        # link is atomic and refuses an existing destination. The temporary is 0600.
        os.link(temporary, path)
    except OSError, ValueError, TypeError:
        _reject("ci_export_failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _reject("ci_export_failed")


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="L0tus-Program/Bees")
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        report = collect(args.repo, args.run, args.expected_sha)
        export(report, args.output)
    except ReportError as error:
        print(json.dumps({"status": "error", "code": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "verified", "run_id": args.run, "head_sha": args.expected_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
