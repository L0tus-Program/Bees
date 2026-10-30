"""Falhas de CI, consulta, logs e publicação nunca viram relatório aprovado."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bees_ci_report", Path(__file__).parents[1] / "ci-report.py"
)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)
REPOSITORY = "L0tus-Program/Bees"
RUN = 123
SHA = "a" * 40
URL = f"https://github.com/{REPOSITORY}/actions/runs/{RUN}"
SECRET = "PRIVATE-LOG-AND-STDERR-MUST-NOT-LEAK"


def metadata():
    jobs = []
    for identity, (name, steps) in enumerate(reporter.REQUIRED_STEPS.items(), 10):
        jobs.append(
            {
                "name": name,
                "databaseId": identity,
                "status": "completed",
                "conclusion": "success",
                "url": f"{URL}/job/{identity}",
                "startedAt": "2026-10-16T18:00:00Z",
                "completedAt": "2026-10-16T18:10:00Z",
                "steps": [
                    {"name": step, "number": number, "status": "completed", "conclusion": "success"}
                    for number, step in enumerate(steps, 1)
                ],
            }
        )
    return {
        "databaseId": RUN,
        "headSha": SHA,
        "status": "completed",
        "conclusion": "success",
        "url": URL,
        "createdAt": "2026-10-16T18:00:00Z",
        "updatedAt": "2026-10-16T18:10:00Z",
        "jobs": jobs,
    }


def logs(job):
    lines = [SECRET]
    for step in reporter.JOBS[job["name"]]:
        summary = (
            " Tests 737 passed (737)"
            if job["name"] == "Interface"
            else "=== 2256 passed, 61 skipped in 120.43s (0:02:00) ==="
        )
        lines.append(f"{job['name']}\t{step}\t2026-10-16T18:10:00.000Z {summary}")
    return "\n".join(lines)


def responses(monkeypatch, value=None, transform=None):
    value = metadata() if value is None else value

    def run(arguments, **kwargs):
        if "--json" in arguments:
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr=SECRET)
        job_id = int(arguments[arguments.index("--job") + 1])
        job = next(job for job in value["jobs"] if job["databaseId"] == job_id)
        content = logs(job)
        if transform:
            content = transform(content)
        return SimpleNamespace(returncode=0, stdout=content, stderr=SECRET)

    monkeypatch.setattr(reporter.subprocess, "run", run)


def collect():
    return reporter.collect(REPOSITORY, RUN, SHA)


def test_complete_jobs_export_only_sanitized_separate_counts(monkeypatch, tmp_path):
    responses(monkeypatch)
    result = collect()
    assert result["status"] == "verified" and result["head_sha"] == SHA
    assert len(result["jobs"]) == 4
    assert "total" not in result
    windows = next(job for job in result["jobs"] if job["name"] == "API / windows-latest")
    assert len(windows["log"]["summaries"]) == 2  # Native subset is not added to full suite.
    path = tmp_path / "report.json"
    reporter.export(result, path)
    content = path.read_text(encoding="utf-8")
    assert SECRET not in content
    assert "stdout" not in content and "stderr" not in content
    assert json.loads(content) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", "cancelled"),
        ("headSha", "b" * 40),
        ("jobs", []),
        ("url", "https://example.test/foreign"),
        ("updatedAt", "truncated"),
    ],
)
def test_bad_or_incomplete_run_is_rejected(monkeypatch, field, value):
    incoming = metadata()
    incoming[field] = value
    responses(monkeypatch, incoming)
    with pytest.raises(reporter.ReportError):
        collect()


@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "pending",
        "duplicate_job",
        "missing_step",
        "failed_step",
        "skipped_required",
        "extra_field",
        "missing_field",
    ],
)
def test_job_and_step_contract_fail_closed(monkeypatch, change):
    incoming = metadata()
    job = incoming["jobs"][0]
    if change == "failed":
        job["conclusion"] = "failure"
    elif change == "pending":
        job["status"] = "in_progress"
    elif change == "duplicate_job":
        incoming["jobs"][1] = copy.deepcopy(job)
    elif change == "missing_step":
        job["steps"] = []
    elif change == "failed_step":
        job["steps"][0]["conclusion"] = "failure"
    elif change == "skipped_required":
        job["steps"][0]["conclusion"] = "skipped"
    elif change == "extra_field":
        job["private"] = SECRET
    elif change == "missing_field":
        del job["completedAt"]
    responses(monkeypatch, incoming)
    with pytest.raises(reporter.ReportError):
        collect()


@pytest.mark.parametrize(
    "failure", ["nonzero", "timeout", "empty", "invalid_json", "truncated_json", "duplicate_json"]
)
def test_metadata_fetch_failure_never_becomes_empty_success(monkeypatch, failure):
    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("gh", 120, stderr=SECRET)
        stdout = {
            "nonzero": json.dumps(metadata()),
            "empty": "",
            "invalid_json": "no",
            "truncated_json": '{"jobs":[',
            "duplicate_json": '{"jobs":[],"jobs":[]}',
        }.get(failure, "")
        return SimpleNamespace(
            returncode=1 if failure == "nonzero" else 0, stdout=stdout, stderr=SECRET
        )

    monkeypatch.setattr(reporter.subprocess, "run", run)
    with pytest.raises(reporter.ReportError) as caught:
        collect()
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "transform",
    [
        lambda value: "",
        lambda value: value[:40],
        lambda value: value + "\n" + value,
        lambda value: value.replace("2256 passed", "1 failed"),
        lambda value: value.replace("737 passed (737)", "736 passed (737)"),
    ],
)
def test_missing_truncated_ambiguous_or_failed_logs_are_rejected(monkeypatch, transform):
    responses(monkeypatch, transform=transform)
    with pytest.raises(reporter.ReportError):
        collect()


def test_log_download_error_produces_no_report_or_secret_output(monkeypatch, tmp_path, capsys):
    def run(arguments, **kwargs):
        return SimpleNamespace(
            returncode=1 if "--log" in arguments else 0,
            stdout="" if "--log" in arguments else json.dumps(metadata()),
            stderr=SECRET,
        )

    monkeypatch.setattr(reporter.subprocess, "run", run)
    output = tmp_path / "never.json"
    assert reporter.main(["--run", str(RUN), "--expected-sha", SHA, "--output", str(output)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"status": "error", "code": "ci_log_fetch_failed"}
    assert captured.out == "" and SECRET not in captured.err and not output.exists()


def test_failed_export_keeps_existing_evidence_and_cleans_temp(monkeypatch, tmp_path, capsys):
    responses(monkeypatch)
    output = tmp_path / "existing.json"
    output.write_text("old evidence")
    assert reporter.main(["--run", str(RUN), "--expected-sha", SHA, "--output", str(output)]) == 1
    assert output.read_text() == "old evidence" and list(tmp_path.glob(".bees-ci-*")) == []
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["code"] == "ci_export_failed"


def test_io_error_during_export_leaves_no_partial_approved_file(monkeypatch, tmp_path):
    responses(monkeypatch)

    def failed_fsync(descriptor):
        raise OSError(SECRET)

    monkeypatch.setattr(reporter.os, "fsync", failed_fsync)
    output = tmp_path / "report.json"
    with pytest.raises(reporter.ReportError, match="ci_export_failed"):
        reporter.export(collect(), output)
    assert not output.exists() and list(tmp_path.glob(".bees-ci-*")) == []


@pytest.mark.parametrize(
    "repository,run,sha",
    [
        ("--/repository", RUN, SHA),
        ("owner/repo/foreign", RUN, SHA),
        (REPOSITORY, 0, SHA),
        (REPOSITORY, True, SHA),
        (REPOSITORY, RUN, "truncated"),
    ],
)
def test_invalid_arguments_never_start_remote_commands(monkeypatch, repository, run, sha):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid arguments must not reach gh")

    monkeypatch.setattr(reporter.subprocess, "run", unexpected)
    with pytest.raises(reporter.ReportError, match="ci_arguments_invalid"):
        reporter.collect(repository, run, sha)


@pytest.mark.parametrize(
    "probe", ["Validar corpus de pesquisa no Windows", "Validar contratos portáveis no Windows"]
)
def test_optional_windows_corpus_probe_skip_is_only_valid_on_linux(monkeypatch, probe):
    value = metadata()
    linux = next(job for job in value["jobs"] if job["name"] == "API / ubuntu-latest")
    step = {
        "name": probe,
        "number": 99,
        "status": "completed",
        "conclusion": "skipped",
    }
    linux["steps"].append(step)
    responses(monkeypatch, value)
    assert collect()["status"] == "verified"
    windows = next(job for job in value["jobs"] if job["name"] == "API / windows-latest")
    windows["steps"].append(step)
    responses(monkeypatch, value)
    with pytest.raises(reporter.ReportError, match="ci_step_not_successful"):
        collect()
