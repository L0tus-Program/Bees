"""Credencial da conta reutilizada sem revelar o valor nem mudar o destino."""

from uuid import UUID

import pytest
from test_onboarding import API_KEY, creation_body, headers, model_body, probe
from test_onboarding import client as client
from test_onboarding import environment as environment

from bees_core.models import Agent
from bees_core.providers.credentials import account_credentials
from bees_core.providers.discovery import DiscoveryAdapter


def create(client):
    response = client.post(
        "/api/v1/agents", json=creation_body(probe(client)), headers=headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()


def selected(client):
    credentials = client.get("/api/v1/model-credentials")
    assert credentials.status_code == 200 and API_KEY not in credentials.text
    entries = credentials.json()["credentials"]
    assert len(entries) == 1 and set(entries[0]) == {"id", "kind", "endpoint"}
    return {"config": model_body()["config"], "credential_id": entries[0]["id"]}


def test_reuse_account_key_for_new_bee_after_original_config_is_removed(
    client, environment, monkeypatch
):
    first = create(client)
    body = selected(client)
    reference = body["credential_id"]
    # Trocar a abelha original para uma conexão sem chave não apaga o catálogo da conta.
    with client.app.state.store.transaction() as unit:
        agent = unit.agents.get(UUID(first["id"]))
        unit.agents.update(agent.model_copy(update={"provider_config": {}}), agent.revision)
    before = len(list(client.app.state.vault.directory.iterdir()))
    token = client.post("/api/v1/models/prepare", json=body, headers=headers(client))
    assert token.status_code == 200, token.text
    result = client.post(
        "/api/v1/agents",
        json={**body, "name": "Segunda", "validation_token": token.json()["validation_token"]},
        headers=headers(client),
    )
    assert result.status_code == 201, result.text
    assert result.json()["provider_config"]["secret_ref"] == reference
    assert len(list(client.app.state.vault.directory.iterdir())) == before
    # Consulta remota também resolve a chave da conta, sem precisar de agent_id.
    transport = environment[1]
    monkeypatch.setattr(
        "bees_api.onboarding.DiscoveryAdapter",
        lambda resolver: DiscoveryAdapter(resolver, transport=transport),
    )
    discovery = client.post(
        "/api/v1/models/discover",
        json={
            "provider_id": "custom",
            "endpoint": body["config"]["endpoint"],
            "credential_id": reference,
        },
        headers=headers(client),
    )
    assert discovery.status_code == 200, discovery.text
    assert client.post("/api/v1/models/test", json=body, headers=headers(client)).status_code == 200
    # Configuração de outra abelha recebe o mesmo vínculo da conta.
    with client.app.state.store.transaction() as unit:
        third = unit.agents.create(Agent(name="Terceira"))
    bound = {**body, "agent_id": str(third.id)}
    token = client.post("/api/v1/models/prepare", json=bound, headers=headers(client)).json()[
        "validation_token"
    ]
    saved = client.post(
        f"/api/v1/agents/{third.id}/configuration",
        json={**bound, "expected_revision": third.revision, "validation_token": token},
        headers=headers(client),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["provider_config"]["secret_ref"] == reference
    assert API_KEY not in saved.text
    with client.app.state.store.transaction(write=False) as unit:
        assert len(account_credentials(unit)) == 1


@pytest.mark.parametrize("change", ["endpoint", "kind", "unknown", "raw", "double"])
def test_account_selection_rejects_destination_change_and_ambiguous_keys(client, change):
    create(client)
    body = selected(client)
    if change == "endpoint":
        body["config"]["endpoint"] = "https://another.example/v1"
    elif change == "kind":
        body["config"]["kind"] = "ollama"
    elif change == "unknown":
        body["credential_id"] = "vault:unknown"
    elif change == "raw":
        body["config"]["secret_ref"] = body.pop("credential_id")
    else:
        body["api_key"] = API_KEY
    response = client.post("/api/v1/models/prepare", json=body, headers=headers(client))
    assert response.status_code in (422, 502), response.text
    assert API_KEY not in response.text


def test_catalog_requires_session_and_receipt_binds_selected_key(client):
    create(client)
    body = selected(client)
    token = client.post("/api/v1/models/prepare", json=body, headers=headers(client)).json()[
        "validation_token"
    ]
    # Mesmo destino mas outra forma de credencial não pode consumir o comprovante.
    changed = {
        "config": body["config"],
        "api_key": API_KEY,
        "name": "Outra",
        "validation_token": token,
    }
    response = client.post("/api/v1/agents", json=changed, headers=headers(client))
    assert response.status_code == 409 and response.json()["error"]["code"] == "validation_required"
    client.cookies.clear()
    assert client.get("/api/v1/model-credentials").status_code == 401
