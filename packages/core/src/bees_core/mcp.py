"""Conectores MCP (Model Context Protocol) por abelha, transporte Streamable HTTP.

Fronteiras:
- Servidor e credencial são configurados por humano; a credencial fica no cofre.
- O catálogo de ferramentas é lido sob demanda humana e guardado; o agente só chama
  ferramentas desse catálogo, com argumentos validados pelo ``inputSchema``.
- ``mcp.call`` é efeito externo: por padrão pede aprovação, com regra opcional por
  servidor e ferramenta. O conteúdo devolvido é dado externo não confiável.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from bees_core.models import utc_now
from bees_core.providers.contracts import is_loopback
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict

PROTOCOL_VERSION = "2025-06-18"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_TOOLS = 128
MAX_RESPONSE = 2_000_000


class McpError(RuntimeError):
    """Códigos: mcp_unreachable, mcp_auth_failed, mcp_protocol_error, mcp_tool_error,
    mcp_unknown_tool, mcp_invalid_arguments, mcp_server_unavailable, mcp_name_taken."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def validate_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or any(ord(c) <= 32 for c in value)
    ):
        raise ValueError("URL MCP exige http(s) sem credenciais.")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise ValueError("HTTP sem TLS somente em loopback.")
    return value


class McpClient:
    """Cliente mínimo: initialize → tools/list | tools/call, JSON ou SSE."""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30,
    ) -> None:
        self.url = validate_url(url)
        self.token = token
        self.transport = transport
        self.timeout = timeout
        self.session_id: str | None = None
        self._next = 0

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(
        self, client: httpx.AsyncClient, payload: dict, *, effect: bool = False
    ) -> dict | None:
        try:
            response = await client.post(self.url, json=payload, headers=self._headers())
        except httpx.ConnectError, httpx.ConnectTimeout:
            raise McpError("mcp_unreachable") from None
        except httpx.HTTPError:
            # Pedido de efeito pode ter chegado ao servidor: resultado incerto.
            raise McpError("mcp_outcome_unknown" if effect else "mcp_unreachable") from None
        if response.status_code in (401, 403):
            raise McpError("mcp_auth_failed")
        if response.status_code >= 400:
            raise McpError(
                "mcp_unreachable" if response.status_code >= 500 else "mcp_protocol_error"
            )
        session = response.headers.get("mcp-session-id")
        if session and len(session) <= 200:
            self.session_id = session
        if "id" not in payload:
            return None
        body = response.content[:MAX_RESPONSE]
        kind = response.headers.get("content-type", "")
        messages: list[Any] = []
        try:
            if kind.startswith("text/event-stream"):
                for block in body.decode("utf-8", "replace").split("\n\n"):
                    data = "\n".join(
                        line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
                    )
                    if data:
                        messages.append(json.loads(data))
            else:
                parsed = json.loads(body)
                messages = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            raise McpError("mcp_protocol_error") from None
        for message in messages:
            if isinstance(message, dict) and message.get("id") == payload["id"]:
                if isinstance(message.get("error"), dict):
                    raise McpError("mcp_tool_error", str(message["error"].get("message", ""))[:500])
                result = message.get("result")
                if not isinstance(result, dict):
                    raise McpError("mcp_protocol_error")
                return result
        raise McpError("mcp_protocol_error")

    async def _request(
        self, client, method: str, params: dict | None = None, *, effect: bool = False
    ) -> dict:
        self._next += 1
        payload = {"jsonrpc": "2.0", "id": self._next, "method": method}
        if params is not None:
            payload["params"] = params
        return await self._post(client, payload, effect=effect)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport, timeout=httpx.Timeout(self.timeout), follow_redirects=False
        )

    async def _initialize(self, client) -> dict:
        result = await self._request(
            client,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bees", "version": "1"},
            },
        )
        await self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def list_tools(self) -> tuple[dict, list[dict]]:
        async with self._client() as client:
            info = await self._initialize(client)
            tools: list[dict] = []
            cursor = None
            for _ in range(10):
                result = await self._request(
                    client, "tools/list", {"cursor": cursor} if cursor else {}
                )
                page = result.get("tools")
                if not isinstance(page, list):
                    raise McpError("mcp_protocol_error")
                tools.extend(item for item in page if isinstance(item, dict))
                cursor = result.get("nextCursor")
                if not isinstance(cursor, str) or len(tools) >= MAX_TOOLS:
                    break
        server = info.get("serverInfo") if isinstance(info.get("serverInfo"), dict) else {}
        return server, [normalize_tool(item) for item in tools[:MAX_TOOLS] if valid_tool(item)]

    async def call(self, tool: str, arguments: dict) -> dict:
        async with self._client() as client:
            await self._initialize(client)
            return await self._request(
                client, "tools/call", {"name": tool, "arguments": arguments}, effect=True
            )


def valid_tool(item: dict) -> bool:
    name = item.get("name")
    schema = item.get("inputSchema", {"type": "object"})
    if not isinstance(name, str) or not 1 <= len(name) <= 128 or not isinstance(schema, dict):
        return False
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return False
    return len(json.dumps(schema)) <= 32768


def normalize_tool(item: dict) -> dict:
    annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    return {
        "name": item["name"],
        "description": str(item.get("description") or "")[:1000],
        "input_schema": item.get("inputSchema") or {"type": "object"},
        "read_only": annotations.get("readOnlyHint") is True,
    }


def result_text(result: dict, limit: int = 12000) -> dict:
    """Resume o resultado MCP em texto + dados estruturados limitados."""
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif item.get("type") == "resource" and isinstance(item.get("resource"), dict):
            resource = item["resource"]
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
        elif item.get("type") in ("image", "audio"):
            parts.append(f"[{item['type']} omitido]")
    text = "\n".join(parts)
    output = {"is_error": result.get("isError") is True, "text": text[:limit]}
    if len(text) > limit:
        output["truncated"] = True
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        encoded = json.dumps(structured, ensure_ascii=False)
        if len(encoded) <= limit:
            output["structured"] = structured
    return output


class CreateServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_request_id: UUID
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=8, max_length=2048)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096, repr=False)

    @field_validator("name")
    @classmethod
    def slug(cls, value):
        if not NAME.fullmatch(value):
            raise ValueError("Use letras minúsculas, números, - ou _.")
        return value

    @field_validator("url")
    @classmethod
    def safe(cls, value):
        return validate_url(value)


COLUMNS = (
    "id,created_at,updated_at,revision,metadata_json,agent_id,name,url,secret_ref,enabled,"
    "status,tools_json,reason_code,refreshed_at"
)


def _row(values) -> dict:
    row = dict(zip(COLUMNS.split(","), values, strict=True))
    row["metadata"] = json.loads(row.pop("metadata_json"))
    row["tools"] = json.loads(row.pop("tools_json"))
    row["enabled"] = bool(row["enabled"])
    return row


def view(row: dict) -> dict:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "name": row["name"],
        "url": row["url"],
        "has_token": row["secret_ref"] is not None,
        "enabled": row["enabled"],
        "status": row["status"],
        "reason_code": row["reason_code"],
        "server_name": row["metadata"].get("server_name"),
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "read_only": tool["read_only"],
            }
            for tool in row["tools"]
        ],
        "refreshed_at": row["refreshed_at"],
        "revision": row["revision"],
    }


class McpService:
    def __init__(
        self,
        database: Database,
        resolver=None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.resolver = resolver
        self.transport = transport
        self.clock = clock

    def _get(self, connection, server_id, agent_id=None) -> dict:
        values = connection.execute(
            f"SELECT {COLUMNS} FROM mcp_servers WHERE id=? AND status!='removed'",
            (str(server_id),),
        ).fetchone()
        if values is None or (agent_id is not None and values[5] != str(agent_id)):
            raise NotFoundError("Servidor MCP não encontrado.")
        return _row(values)

    def get(self, agent_id, server_id) -> dict:
        with self.database.transaction(write=False) as connection:
            return self._get(connection, server_id, agent_id)

    def list(self, agent_id, *, enabled_only=False) -> list[dict]:
        with self.database.transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM mcp_servers WHERE agent_id=? AND status!='removed' "
                "AND (?=0 OR (enabled=1 AND status='ready')) ORDER BY name",
                (str(agent_id), 1 if enabled_only else 0),
            ).fetchall()
        return [_row(values) for values in rows]

    def by_name(self, agent_id, name: str) -> dict:
        for row in self.list(agent_id, enabled_only=True):
            if row["name"] == name:
                return row
        raise McpError("mcp_server_unavailable")

    def client(self, row: dict) -> McpClient:
        token = None
        if row["secret_ref"]:
            if self.resolver is None:
                raise McpError("mcp_auth_failed")
            token = self.resolver.resolve(row["secret_ref"]).get_secret_value()
        return McpClient(row["url"], token, transport=self.transport)

    def create(self, agent_id, value: CreateServer, *, secret_ref, server: dict, tools) -> dict:
        now = _iso(self.clock())
        server_id = uuid5(value.client_request_id, "bees.mcp-server/v1")
        with self.database.transaction() as connection:
            if (
                connection.execute("SELECT 1 FROM agents WHERE id=?", (str(agent_id),)).fetchone()
                is None
            ):
                raise NotFoundError("Abelha não encontrada.")
            if connection.execute(
                "SELECT 1 FROM mcp_servers WHERE agent_id=? AND name=? AND status!='removed'",
                (str(agent_id), value.name),
            ).fetchone():
                raise McpError("mcp_name_taken")
            connection.execute(
                f"INSERT INTO mcp_servers({COLUMNS}) VALUES(?,?,?,1,?,?,?,?,?,1,'ready',?,NULL,?)",
                (
                    str(server_id),
                    now,
                    now,
                    json.dumps(
                        {
                            "client_request_id": str(value.client_request_id),
                            "server_name": str(server.get("name") or "")[:100] or None,
                        }
                    ),
                    str(agent_id),
                    value.name,
                    value.url,
                    secret_ref,
                    json.dumps(tools),
                    now,
                ),
            )
            return self._get(connection, server_id)

    def find_request(self, client_request_id: UUID) -> dict | None:
        with self.database.transaction(write=False) as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM mcp_servers "
                "WHERE json_extract(metadata_json,'$.client_request_id')=?",
                (str(client_request_id),),
            ).fetchone()
        return _row(values) if values else None

    def _update(self, agent_id, server_id, expected_revision, **fields) -> dict:
        with self.database.transaction() as connection:
            row = self._get(connection, server_id, agent_id)
            if expected_revision is not None and row["revision"] != expected_revision:
                raise RevisionConflict("Servidor MCP alterado; atualize a tela.")
            fields["updated_at"] = _iso(self.clock())
            assignments = ",".join(f"{key}=?" for key in fields)
            connection.execute(
                f"UPDATE mcp_servers SET {assignments},revision=revision+1 WHERE id=?",
                (*fields.values(), row["id"]),
            )
            if fields.get("status") == "removed":
                return row | {"status": "removed", "enabled": False}
            return self._get(connection, server_id)

    def set_enabled(self, agent_id, server_id, expected_revision: int, enabled: bool) -> dict:
        return self._update(agent_id, server_id, expected_revision, enabled=1 if enabled else 0)

    def remove(self, agent_id, server_id, expected_revision: int) -> dict:
        return self._update(agent_id, server_id, expected_revision, status="removed", enabled=0)

    async def refresh(self, agent_id, server_id, expected_revision: int) -> dict:
        row = self.get(agent_id, server_id)
        if row["revision"] != expected_revision:
            raise RevisionConflict("Servidor MCP alterado; atualize a tela.")
        try:
            server, tools = await self.client(row).list_tools()
        except McpError as error:
            return self._update(
                agent_id, server_id, expected_revision, status="failed", reason_code=error.code
            )
        return self._update(
            agent_id,
            server_id,
            expected_revision,
            status="ready",
            reason_code=None,
            tools_json=json.dumps(tools),
            refreshed_at=_iso(self.clock()),
        )

    async def call(self, agent_id, server: str, tool: str, arguments: dict) -> dict:
        row = self.by_name(agent_id, server)
        known = next((item for item in row["tools"] if item["name"] == tool), None)
        if known is None:
            raise McpError("mcp_unknown_tool")
        errors = sorted(Draft202012Validator(known["input_schema"]).iter_errors(arguments), key=str)
        if errors:
            raise McpError("mcp_invalid_arguments", errors[0].message[:300])
        result = await self.client(row).call(tool, arguments)
        return {"server": server, "tool": tool} | result_text(result)
