"""Saúde das conexões: só estados que exigem ação humana, sem segredos."""

from datetime import timedelta
from uuid import uuid4

from pydantic import SecretStr
from test_tasks_execution import fixture_state

from bees_core.channels.adapters import Identity
from bees_core.channels.service import ChannelService, CreateChannel
from bees_core.connection_health import connection_issues
from bees_core.executors import ExecutorService
from bees_core.mcp import CreateServer, McpService
from bees_core.models import utc_now


def test_lists_failed_channel_mcp_and_offline_computer(tmp_path):
    database, store, agent, _, _ = fixture_state(tmp_path)
    assert connection_issues(database, agent.id) == []
    channels = ChannelService(database)
    link, _ = channels.create(
        agent.id,
        CreateChannel(client_request_id=uuid4(), kind="telegram", token=SecretStr("1" * 30)),
        secret_ref="vault:x",
        identity=Identity(id="1", name="Bot"),
    )
    channels.fail(link["id"], "channel_token_invalid")
    mcp = McpService(database)
    server = mcp.create(
        agent.id,
        CreateServer(client_request_id=uuid4(), name="agenda", url="https://mcp.example.org/m"),
        secret_ref="vault:segredo",
        server={},
        tools=[],
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE mcp_servers SET status='failed',reason_code='mcp_auth_failed' WHERE id=?",
            (server["id"],),
        )
    clock = [utc_now()]
    executors = ExecutorService(database, clock=lambda: clock[0])
    invite = executors.invite({"kind": "computer_runtime", "name": "Runtime"})
    paired = executors.exchange({"token": invite["token"]})
    executors.touch(paired["executor_id"], "c1")
    computer = executors.create_computer(agent.id, {"client_request_id": uuid4()})
    executors.report_computer(paired["executor_id"], computer["id"], "ready")
    assert {item["kind"] for item in connection_issues(database, agent.id)} == {"channel", "mcp"}
    later = utc_now() + timedelta(minutes=5)
    issues = connection_issues(database, agent.id, now=later)
    kinds = {item["kind"]: item for item in issues}
    assert set(kinds) == {"channel", "mcp", "computer"}
    assert kinds["channel"]["code"] == "channel_token_invalid"
    assert kinds["mcp"]["code"] == "mcp_auth_failed"
    assert kinds["computer"]["code"] == "runtime_offline"
    assert "vault:" not in str(issues) and "segredo" not in str(issues)
