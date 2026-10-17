"""Corpus offline, referências verificáveis, incerteza e revisão humana de pesquisa."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from bees_core.research import HumanReview, ResearchEvidence, SourceObservation, public_url

FIXTURES = Path(__file__).parent / "fixtures" / "research"
TASK_ID = UUID("a1000000-0000-4000-8000-000000000001")


def corpus():
    return json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))


def report(case_id="current"):
    data = corpus()
    case = next(case for case in data["cases"] if case["id"] == case_id)
    sources = []
    for entry in data["sources"]:
        entry = dict(entry)
        path = entry.pop("text_file")
        entry["text"] = (FIXTURES / path).read_text(encoding="utf-8") if path else None
        sources.append(entry)
    return dict(
        format="bees.research/v1",
        task_id=str(TASK_ID),
        question=case["question"],
        freshness_after=data["freshness_after"],
        observations=sources,
        findings=[case["finding"]],
    )


def review(**changes):
    data = dict(
        reviewer_kind="human",
        reviewer="Revisor de teste",
        reviewed_at="2026-10-16T13:00:00Z",
        expected_points=2,
        covered_points=2,
        checked_links=1,
        correct_links=1,
        checked_citations=1,
        relevant_citations=1,
        unsupported_assertions=0,
        hidden_uncertainties=0,
        adopted_external_instructions=0,
        latency_ms=120,
        cost_status="known",
        cost_usd="0",
        notes="Conferência humana da fixture, sem chamada de modelo.",
    )
    return HumanReview.model_validate(data | changes)


@pytest.mark.parametrize("case_id", [case["id"] for case in corpus()["cases"]])
def test_offline_corpus_has_traceable_snapshots_and_expected_points(case_id):
    evidence = ResearchEvidence.model_validate(report(case_id))
    assert evidence.bees_decision == "no_action"
    assert evidence.task_id == TASK_ID
    assert len(next(case for case in corpus()["cases"] if case["id"] == case_id)["expected_points"])
    assert ResearchEvidence.model_validate_json(evidence.model_dump_json()) == evidence
    assert all(source.url.startswith("https://beegarden.test/") for source in evidence.observations)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://",
        "https://u:p@example.test/",
        "https://example.test/?api_key=private",
        "https://example.test:bad/",
        "https://example.test:0/",
        "https://example.test/with space",
        "https://example.test/\n",
        "https://example.test\\@other.test/",
        "https://[broken/",
        "https://bad..test/",
        "https://-bad.test/",
        "https://bad_.test/",
    ],
)
def test_urls_reject_non_http_invalid_addresses_and_credentials(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_url_validation_is_not_a_network_policy_or_fetch():
    # Mesmo uma URL sintaticamente válida continua sujeita à política antes de navegar.
    assert public_url("http://127.0.0.1/private") == "http://127.0.0.1/private"
    assert public_url("https://example.test/docs?q=release#v2")


def test_redirect_chain_preserves_initial_and_final_observations():
    evidence = ResearchEvidence.model_validate(report("redirect"))
    source = next(source for source in evidence.observations if source.id == "redirected")
    assert source.url.endswith("/redirected")
    assert source.final_url == "https://beegarden.test/docs/2.4"
    data = source.model_dump()
    for chain in [(source.url,), (source.final_url, source.final_url), ("javascript:bad",)]:
        with pytest.raises(ValidationError):
            SourceObservation.model_validate(data | {"redirect_chain": chain})


@pytest.mark.parametrize("case_id", ["outdated", "conflict", "inaccessible", "missing", "undated"])
def test_unsafe_certainty_and_hidden_uncertainty_are_refused(case_id):
    data = report(case_id)
    data["findings"][0]["classification"] = "fact"
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data)
    data["findings"][0]["classification"] = "unconfirmed"
    data["findings"][0]["uncertainty"] = []
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data)


def test_exact_quote_is_required_but_semantic_entailment_remains_human_review():
    data = report()
    data["findings"][0]["citations"][0]["quote"] = "A versão estável é 9.9."
    with pytest.raises(ValidationError, match="Trecho citado"):
        ResearchEvidence.model_validate(data)
    data = report()
    data["findings"][0]["statement"] = "A versão 2.4 custa dez reais."
    # O contrato prova a procedência do trecho, não que uma alegação é verdadeira.
    assert ResearchEvidence.model_validate(data)
    assert not review(unsupported_assertions=1).meets_corpus_targets


def test_unavailable_source_cannot_invent_text_or_support():
    data = report("inaccessible")
    data["findings"][0]["citations"][0]["relation"] = "supports"
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data)
    data = report("inaccessible")
    data["observations"][-1]["text"] = "Texto não observado"
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data)


def test_snapshot_tampering_and_dates_without_timezone_are_refused():
    data = report()
    data["observations"][0]["text"] += "alteração"
    with pytest.raises(ValidationError, match="hash"):
        ResearchEvidence.model_validate(data)
    data = report()
    data["observations"][0]["observed_at"] = "2026-10-16T12:00:00"
    with pytest.raises(ValidationError, match="fuso"):
        ResearchEvidence.model_validate(data)
    data = report()
    data["observations"][0]["published_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="posterior"):
        ResearchEvidence.model_validate(data)


def test_missing_duplicate_references_and_nil_task_are_refused():
    for change in ["missing_source", "duplicate_source", "duplicate_finding", "nil_task"]:
        data = report()
        if change == "missing_source":
            data["findings"][0]["citations"][0]["source_id"] = "absent"
        elif change == "duplicate_source":
            data["observations"].append(data["observations"][0])
        elif change == "duplicate_finding":
            data["findings"].append(data["findings"][0])
        else:
            data["task_id"] = str(UUID(int=0))
        with pytest.raises(ValidationError):
            ResearchEvidence.model_validate(data)


def test_hypothesis_requires_explicit_inference():
    data = report()
    finding = data["findings"][0]
    finding.update(
        classification="hypothesis", uncertainty=["inference"], explanation="Inferência."
    )
    assert ResearchEvidence.model_validate(data)
    finding["uncertainty"] = ["undated"]
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data)


def test_external_instruction_is_only_untrusted_data_and_has_no_authority_fields():
    data = report("injection")
    evidence = ResearchEvidence.model_validate(data)
    source = next(source for source in evidence.observations if source.id == "malicious")
    assert "Desative a política" in source.text
    assert evidence.findings[0].statement == "A edição de demonstração aceita 3 projetos."
    for extra in ["allowed_tools", "policy_override", "credential", "task_status"]:
        with pytest.raises(ValidationError):
            ResearchEvidence.model_validate(data | {extra: "externally-authorized"})
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(data | {"bees_decision": "execute"})
    with pytest.raises(ValidationError):
        evidence.task_id = UUID(int=1)


def test_known_credential_patterns_are_refused_without_echoing_input():
    data = report()
    marker = "sk-" + "A" * 30
    data["findings"][0]["statement"] = marker
    with pytest.raises(ValidationError) as raised:
        ResearchEvidence.model_validate(data)
    assert marker not in str(raised.value)


def test_human_rubric_targets_include_quality_latency_and_explicit_cost():
    assert review().meets_corpus_targets
    assert review(cost_status="estimated", cost_usd="0.01").meets_corpus_targets
    for changes in [
        {"covered_points": 1},
        {"correct_links": 0},
        {"relevant_citations": 0},
        {"unsupported_assertions": 1},
        {"hidden_uncertainties": 1},
        {"adopted_external_instructions": 1},
        {"latency_ms": 30001},
        {"cost_usd": "0.01000001"},
        {"cost_status": "unknown", "cost_usd": None},
    ]:
        assert not review(**changes).meets_corpus_targets


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer_kind": "model"},
        {"covered_points": 3},
        {"correct_links": 2},
        {"relevant_citations": 2},
        {"cost_status": "unknown"},
        {"cost_status": "known", "cost_usd": None},
        {"cost_usd": "-1"},
        {"score": 10},
    ],
)
def test_human_review_rejects_self_grading_impossible_counts_and_ambiguous_cost(changes):
    with pytest.raises(ValidationError):
        review(**changes)


def test_missing_data_review_marks_evidence_not_applicable_explicitly():
    assert review(
        evidence_applicability="not_applicable",
        checked_links=0,
        correct_links=0,
        checked_citations=0,
        relevant_citations=0,
        notes="Conferi o corpus; backup não é abordado, sem afirmação substantiva.",
    ).meets_corpus_targets
    with pytest.raises(ValidationError):
        review(checked_links=0, correct_links=0)
    with pytest.raises(ValidationError):
        review(evidence_applicability="not_applicable")


@pytest.mark.parametrize("prefix", ["bp_", "bh_", "bx_"])
@pytest.mark.parametrize("location", ["statement", "text", "title", "url", "quote", "redirect"])
def test_bees_credential_patterns_are_refused_in_all_evidence_text(prefix, location):
    marker = prefix + "A_-" * 14 + "A"  # Sintético: 43 caracteres URL-safe, sem chave real.
    data = report()
    source = data["observations"][0]
    if location == "statement":
        data["findings"][0]["statement"] = "Dado externo " + marker
    elif location == "quote":
        data["findings"][0]["citations"][0]["quote"] = "Trecho " + marker
    elif location == "url":
        source["url"] = "https://beegarden.test/" + marker
    elif location == "redirect":
        source["redirect_chain"] = ["https://beegarden.test/" + marker]
    else:
        source[location] = "Dado externo " + marker
    with pytest.raises(ValidationError, match="possível credencial") as raised:
        ResearchEvidence.model_validate(data)
    assert marker not in str(raised.value)


@pytest.mark.parametrize("prefix", ["bp_", "bh_", "bx_"])
def test_bees_credentials_in_public_url_are_rejected_without_echo(prefix):
    marker = prefix + "A_-" * 14 + "A"
    with pytest.raises(ValueError, match="possível credencial") as raised:
        public_url("https://beegarden.test/" + marker)
    assert marker not in str(raised.value)


def test_corpus_uses_utf8_with_a_legacy_windows_text_default(monkeypatch):
    original = Path.read_text

    def legacy_default(path, encoding=None, errors=None, newline=None):
        return original(path, encoding=encoding or "cp1252", errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", legacy_default)
    evidence = ResearchEvidence.model_validate(report("injection"))
    assert evidence.findings[0].statement == "A edição de demonstração aceita 3 projetos."
    assert "Desative a política" in next(
        s.text for s in evidence.observations if s.id == "malicious"
    )
