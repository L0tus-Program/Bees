"""Worker textual durável: fila SQLite, rede fora de transações e fencing."""

import asyncio
import math
import time
from uuid import UUID, uuid4

import httpx

from bees_core.approvals import ApprovalService
from bees_core.budgets import fits, releases, reserve, settle_safely
from bees_core.models import ExecutionClaim, ModelCall, utc_now
from bees_core.providers.base import create_adapter, validate_bearer_secret
from bees_core.providers.contracts import SecretResolver
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import PreparedChat, ProviderService
from bees_core.safety import blocked, ensure_running
from bees_core.storage.database import Database
from bees_core.storage.store import RevisionConflict, StateStore


class _CancelledByUser(Exception):
    pass


class TaskWorker:
    """Uma geração textual por tarefa; redirects criam novas chamadas limitadas.

    Globalmente um despacho coordenado por vez. A lease não prova que um
    provedor parou após timeout/crash. Não há retries automáticos pós-despacho.
    """

    def __init__(
        self,
        database: Database,
        resolver: SecretResolver | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        owner_id: UUID | None = None,
        poll_seconds: float = 0.5,
        lease_seconds: int = 30,
    ) -> None:
        if not 0.01 <= poll_seconds <= 5 or not 5 <= lease_seconds <= 300:
            raise ValueError("Cadência ou lease de worker inválida.")
        self.store = StateStore(database)
        self.providers = ProviderService(database, resolver, transport=transport)
        self.approvals = ApprovalService(database, resolver=resolver)
        self.owner_id = owner_id or uuid4()
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds

    @staticmethod
    def _instruction(task, run) -> str:
        parts = [
            "Execute uma tarefa textual usando somente as informações disponíveis nesta conversa. "
            "Entregue o resultado diretamente. Não alegue navegação, pesquisa externa, "
            "uso de aplicativos ou execução de ferramentas.",
            "O propósito permanente da abelha orienta seu comportamento. "
            "O pedido atual é a tarefa abaixo: produza a entrega solicitada, "
            "sem substituir a tarefa por uma descrição do seu propósito.",
            f"Título da tarefa: {task.title}",
            f"Objetivo: {task.objective}",
        ]
        if task.expected_result:
            parts.append(f"Resultado esperado: {task.expected_result}")
        if run.checkpoint.get("latest_instruction"):
            parts.append(
                f"Redirecionamento atual do usuário: {run.checkpoint['latest_instruction']}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _directive(run) -> int:
        return int(run.checkpoint.get("directive_revision", 0))

    def _prepare(self, claim: ExecutionClaim) -> tuple[ModelCall, PreparedChat] | None:
        with self.store.transaction(actor="worker", source="task_prepare") as uow:
            now = utc_now()
            uow.execution.assert_claim(claim, now=now)
            task = uow.tasks.get(claim.task.id)
            run = uow.runs.get(claim.run.id)
            if task.metadata.get("execution_kind") != "text_task":
                self._settle_local(uow, claim, code="unsupported_task_kind")
                return None
            if task.desired_state != "running":
                self._settle_local(uow, claim, code=None)
                return None
            agent = self.providers._agent(uow, task.agent_id)
            if (
                agent.revision != run.metadata.get("agent_revision")
                or agent.provider_config != run.provider_config
                or agent.status != "active"
            ):
                self._settle_local(uow, claim, code="configuration_changed")
                return None
            calls = uow.model_calls.list(run_id=run.id, limit=1000)
            prepared_calls = [call for call in calls if call.status == "prepared"]
            prepared = self.approvals.prepared_for_run(uow, task, run)
            for previous in prepared_calls:
                if prepared is None and previous.snapshot.get(
                    "directive_revision"
                ) == self._directive(run):
                    prepared = PreparedChat.model_validate(previous.snapshot["prepared"])
                    self.providers.validate_snapshot(uow, prepared)
                uow.execution.discard_prepared(claim, previous.id, now=now)
            if prepared is None:
                prepared = self.providers.prepare_chat(
                    uow,
                    task.agent_id,
                    task.conversation_id,
                    self._instruction(task, run),
                    task_id=task.id,
                )
            if prepared.config.secret_ref is not None:
                validate_bearer_secret(self.providers.resolver.resolve(prepared.config.secret_ref))
            snapshot = {
                "prepared": prepared.model_dump(mode="json"),
                "directive_revision": self._directive(run),
            }
            config = prepared.config.model_dump(mode="json")
            request = prepared.request.model_dump(mode="json")
            call = uow.model_calls.create(
                ModelCall(
                    run_id=run.id,
                    ordinal=max((entry.ordinal for entry in calls), default=0) + 1,
                    task_control_revision=task.control_revision,
                    agent_revision=agent.revision,
                    lease_generation=claim.generation,
                    provider_config=config,
                    request=request,
                    snapshot=snapshot,
                    request_hash=uow.model_calls.request_digest(config, request, snapshot),
                )
            )
            uow.runs.update(
                run.model_copy(update={"checkpoint": run.checkpoint | {"progress": "prepared"}}),
                expected_revision=run.revision,
            )
            return call, prepared

    def _wait_for_approval(self, uow, claim, prepared, decision):
        task = uow.tasks.get(claim.task.id)
        run = uow.runs.get(claim.run.id)
        self.approvals.request(uow, task, run, prepared, decision)
        self._settle_local(uow, claim, code="policy_approval_required")

    def _settle_local(self, uow, claim, *, code: str | None) -> None:
        """Parada antes de despacho; não há chamada a repetir/reconciliar."""
        task = uow.tasks.get(claim.task.id)
        run = uow.runs.get(claim.run.id)
        for call in uow.model_calls.list(run_id=run.id, status="prepared", limit=1000):
            uow.execution.discard_prepared(claim, call.id, now=utc_now(), error_code=code)
        status = (
            "cancelled"
            if task.desired_state == "cancelled"
            else "waiting_approval"
            if code == "policy_approval_required"
            else "paused"
        )
        task = uow.tasks.update(
            task.model_copy(
                update={
                    "status": status,
                    "desired_state": "cancelled" if status == "cancelled" else "paused",
                }
            ),
            expected_revision=task.revision,
        )
        checkpoint = run.checkpoint | {"progress": status, "attention_required": code is not None}
        uow.runs.update(
            run.model_copy(
                update={
                    "status": status,
                    "error": code,
                    "checkpoint": checkpoint,
                    "finished_at": utc_now() if status == "cancelled" else None,
                }
            ),
            expected_revision=run.revision,
        )
        uow.execution.release(claim)

    def _settle_stopped(self, uow, claim, call):
        """Parada global antes do efeito: tarefa volta à fila, sem pausa nem chamada."""
        uow.execution.assert_claim(claim, now=utc_now())
        task = uow.tasks.get(claim.task.id)
        run = uow.runs.get(claim.run.id)
        uow.execution.discard_prepared(claim, call.id, now=utc_now(), error_code="global_stop")
        if task.desired_state != "running":
            self._settle_local(uow, claim, code=None)
            return
        uow.tasks.update(task.model_copy(update={"status": "queued"}), task.revision)
        uow.runs.update(
            run.model_copy(
                update={"status": "queued", "checkpoint": run.checkpoint | {"progress": "queued"}}
            ),
            run.revision,
        )
        uow.execution.release(claim)

    def _settle_conflict(self, uow, claim, call, elapsed_ms):
        uow.execution.assert_claim(claim, now=utc_now())
        uow.execution.charge_active(claim, elapsed_ms, now=utc_now())
        task = uow.tasks.get(claim.task.id)
        run = uow.runs.get(claim.run.id)
        if (
            task.desired_state == "running"
            and self._directive(run) != call.snapshot["directive_revision"]
        ):
            uow.execution.discard_prepared(claim, call.id, now=utc_now())
            uow.tasks.update(task.model_copy(update={"status": "queued"}), task.revision)
            uow.runs.update(run.model_copy(update={"status": "queued"}), run.revision)
            uow.execution.release(claim)
        else:
            self._settle_local(
                uow, claim, code=None if task.desired_state != "running" else "context_changed"
            )

    async def _dispatch(self, claim, prepared, remaining_seconds, calls, started, generation):
        """Monitore controle/lease sem manter uma UoW aberta durante o await."""

        def authorize_generation():
            # Também roda depois do preflight Ollama: políticas, controle e contexto
            # podem ter mudado durante essa consulta. Journal precede o efeito real.
            if calls[0].status != "prepared":
                raise ProviderError("state_conflict")
            with self.store.transaction(actor="worker", source="task_dispatch") as uow:
                # Parada ou nova geração desde o claim: nada é enviado ao provedor.
                ensure_running(uow, generation)
                decision = self.providers.model_policy(uow, prepared)
                uow.execution.assert_claim(claim, now=utc_now())
                task = uow.tasks.get(claim.task.id)
                run = uow.runs.get(claim.run.id)
                if (
                    task.desired_state != "running"
                    or task.control_revision != calls[0].task_control_revision
                ):
                    raise RevisionConflict("Controle mudou antes da geração autorizada.")
                approval = self.approvals.check(uow, task, run, prepared, decision)
                if approval is None:
                    self.providers.require_model_policy(decision)
                if time.monotonic() - started >= (
                    task.max_active_seconds - task.active_milliseconds / 1000
                ):
                    raise ProviderError("timeout")
                consumed = (
                    self.approvals.consume(
                        uow,
                        task,
                        run,
                        prepared,
                        decision,
                        call_id=calls[0].id,
                    )
                    if approval is not None
                    else None
                )
                # Orçamento esgotado reverte este commit inteiro, inclusive o contador.
                reserve(uow, prepared, source="task", model_call_id=calls[0].id)
                call = uow.execution.begin_call(claim, calls[0], now=utc_now())
                call = uow.model_calls._change(
                    call,
                    metadata=call.metadata
                    | ({"approval_id": str(consumed.id)} if consumed is not None else {})
                    | {
                        "policy": {
                            "rules_hash": decision.rules_hash,
                            "rule_ids": [str(value) for value in decision.deciding_policy_ids],
                        }
                    },
                )
                run = uow.runs.get(claim.run.id)
                uow.runs.update(
                    run.model_copy(
                        update={"checkpoint": run.checkpoint | {"progress": "generating"}}
                    ),
                    expected_revision=run.revision,
                )
            calls[0] = call

        adapter = create_adapter(
            prepared.config.kind,
            self.providers.resolver,
            transport=self.providers.transport,
            before_generation=authorize_generation,
        )
        operation = asyncio.create_task(adapter.complete(prepared.config, prepared.request))
        next_renew = time.monotonic() + min(5, self.lease_seconds / 3)
        deadline = time.monotonic() + remaining_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError("timeout")
                finished, _ = await asyncio.wait(
                    {operation}, timeout=min(self.poll_seconds, remaining)
                )
                with self.store.transaction(actor="worker", source="task_heartbeat") as uow:
                    now = utc_now()
                    uow.execution.assert_claim(claim, now=now)
                    task = uow.tasks.get(claim.task.id)
                    if task.desired_state == "cancelled" and not finished:
                        raise _CancelledByUser
                    if time.monotonic() >= next_renew:
                        claim = uow.execution.renew(claim, now=now, ttl_seconds=self.lease_seconds)
                        next_renew = time.monotonic() + min(5, self.lease_seconds / 3)
                if finished:
                    return operation.result(), claim
        finally:
            if not operation.done():
                operation.cancel()
            # O adapter fecha transporte em cancellation. Nunca deixar await órfão.
            await asyncio.gather(operation, return_exceptions=True)

    def _finish(
        self,
        claim,
        call,
        prepared,
        *,
        elapsed_ms,
        response=None,
        error_code=None,
        usage=None,
        released=False,
    ):
        with self.store.transaction(actor="worker", source="task_result") as uow:
            now = utc_now()
            uow.execution.assert_claim(claim, now=now)
            entry = uow.usage_entries.for_model_call(call.id)
            if entry is not None:
                # Falha de liquidação não pode desfazer o resultado; a reserva segue contada.
                settle_safely(
                    uow,
                    entry.id,
                    usage=response.usage if response is not None else usage,
                    released=released,
                    now=now,
                )
            task = uow.execution.charge_active(claim, elapsed_ms, now=now)
            run = uow.runs.get(claim.run.id)
            output = None
            obsolete = False
            context_changed = False
            if response is not None:
                response = self.providers.normalized_response(prepared, response)
                obsolete = self._directive(run) != call.snapshot["directive_revision"]
                if task.desired_state == "cancelled":
                    obsolete = True
                if not obsolete:
                    try:
                        self.providers.validate_snapshot(uow, prepared)
                    except ProviderError:
                        obsolete = context_changed = True
                    if not obsolete:
                        output = self.providers.persist_response(uow, prepared, response)
            uow.execution.finish_call(
                claim,
                call.id,
                expected_revision=call.revision,
                status="confirmed" if response is not None else "outcome_unknown",
                response=response.model_dump(mode="json") if response is not None else None,
                output_message_id=output.id if output is not None else None,
                error_code=error_code,
                now=now,
                obsolete=obsolete,
            )
            if call.metadata.get("approval_id"):
                self.approvals.finish(
                    uow,
                    UUID(call.metadata["approval_id"]),
                    "confirmed" if response is not None else "outcome_unknown",
                )
            checkpoint = run.checkpoint | {"attention_required": False}
            status = "completed"
            desired = task.desired_state
            error = None
            if desired == "cancelled":
                status = "cancelled"
                checkpoint["attention_required"] = response is None
                error = "outcome_unknown" if response is None else None
            elif response is None:
                status = desired = "paused"
                checkpoint["attention_required"] = True
                error = "outcome_unknown"
            elif context_changed:
                status = desired = "paused"
                checkpoint["attention_required"] = True
                error = "context_changed"
            elif obsolete:
                checkpoint["result_ready"] = False
                if desired == "paused":
                    status = "paused"
                elif (
                    task.calls_started >= task.max_calls
                    or task.active_milliseconds >= task.max_active_seconds * 1000
                ):
                    status = desired = "paused"
                    checkpoint["attention_required"] = True
                    error = "task_limit_reached"
                else:
                    status = "queued"
            elif response.finish_reason != "stop":
                status = desired = "paused"
                checkpoint["attention_required"] = True
                error = (
                    "response_truncated"
                    if response.finish_reason == "length"
                    else "content_filtered"
                )
            elif not response.message.content or not response.message.content.strip():
                status = desired = "paused"
                checkpoint["attention_required"] = True
                error = "empty_response"
            elif desired == "paused":
                status = "paused"
            if output is not None:
                checkpoint["result_message_id"] = str(output.id)
                checkpoint["result_ready"] = response.finish_reason == "stop" and bool(
                    response.message.content and response.message.content.strip()
                )
            checkpoint["progress"] = status
            uow.tasks.update(
                task.model_copy(update={"status": status, "desired_state": desired}),
                expected_revision=task.revision,
            )
            uow.runs.update(
                run.model_copy(
                    update={
                        "status": status,
                        "error": error,
                        "checkpoint": checkpoint,
                        "finished_at": now if status in ("completed", "cancelled") else None,
                    }
                ),
                expected_revision=run.revision,
            )
            uow.execution.release(claim)

    async def run_once(self) -> bool:
        with self.store.transaction(actor="worker", source="task_claim") as uow:
            claim = uow.execution.claim_next(
                self.owner_id, now=utc_now(), ttl_seconds=self.lease_seconds
            )
            generation = uow.safety.state().generation
        if claim is None:
            return False
        start = time.monotonic()
        try:
            result = self._prepare(claim)
        except ProviderError as error:
            with self.store.transaction(actor="worker", source="task_preflight_error") as uow:
                uow.execution.assert_claim(claim, now=utc_now())
                self._settle_local(uow, claim, code=error.code)
            return True
        if result is None:
            return True
        call, prepared = result
        try:
            with self.store.transaction(actor="worker", source="task_dispatch") as uow:
                if blocked(uow.safety.state(), generation):
                    self._settle_stopped(uow, claim, call)
                    return True
                self.providers.validate_snapshot(uow, prepared)
                if prepared.request.tools or any(
                    message.tool_calls or message.role == "tool"
                    for message in prepared.request.messages
                ):
                    raise ProviderError("unsupported_capability")
                if time.monotonic() - start >= (
                    claim.task.max_active_seconds - claim.task.active_milliseconds / 1000
                ):
                    uow.execution.charge_active(
                        claim, math.ceil((time.monotonic() - start) * 1000), now=utc_now()
                    )
                    self._settle_local(uow, claim, code="task_limit_reached")
                    return True
                decision = self.providers.model_policy(uow, prepared)
                current_task = uow.tasks.get(claim.task.id)
                current_run = uow.runs.get(claim.run.id)
                if (
                    current_task.desired_state != "running"
                    or current_task.control_revision != call.task_control_revision
                ):
                    raise RevisionConflict("Controle mudou antes de pedir uma decisão.")
                if decision.effect != "deny" and not fits(uow, prepared):
                    # Antes de pedir decisão humana: sem orçamento a geração não aconteceria.
                    self._settle_local(uow, claim, code="budget_exhausted")
                    return True
                approval = self.approvals.check(uow, current_task, current_run, prepared, decision)
                if decision.effect == "ask" and approval is None:
                    self._wait_for_approval(uow, claim, prepared, decision)
                    return True
                if decision.effect == "deny":
                    self._settle_local(
                        uow,
                        claim,
                        code="policy_denied",
                    )
                    return True
        except ProviderError, RevisionConflict:
            with self.store.transaction(actor="worker", source="task_dispatch_conflict") as uow:
                self._settle_conflict(
                    uow, claim, call, math.ceil((time.monotonic() - start) * 1000)
                )
            return True
        remaining = (
            claim.task.max_active_seconds
            - claim.task.active_milliseconds / 1000
            - (time.monotonic() - start)
        )
        response = None
        error_code = None
        usage = None
        released = False
        dispatched = [call]
        try:
            response, claim = await self._dispatch(
                claim, prepared, remaining, dispatched, start, generation
            )
            # Uma resposta recusada pelo contrato ainda consumiu o que o provedor informou.
            usage = response.usage
            response = self.providers.normalized_response(prepared, response)
            if response.message.tool_calls:
                raise ProviderError("invalid_response")
        except _CancelledByUser:
            error_code = "cancelled_after_dispatch"
        except ProviderError as error:
            response = None
            error_code = error.code
            released = usage is None and releases(error)
        except RevisionConflict:
            # Outro dono/recuperação ganhou a lease; nenhum commit pelo dono antigo.
            if dispatched[0].status != "prepared":
                raise
            with self.store.transaction(actor="worker", source="task_dispatch_conflict") as uow:
                self._settle_conflict(
                    uow, claim, dispatched[0], math.ceil((time.monotonic() - start) * 1000)
                )
            return True
        except asyncio.CancelledError:
            if dispatched[0].status == "prepared":
                with self.store.transaction(actor="worker", source="task_shutdown") as uow:
                    self._settle_local(uow, claim, code="worker_shutdown")
                raise
            self._finish(
                claim,
                dispatched[0],
                prepared,
                elapsed_ms=math.ceil((time.monotonic() - start) * 1000),
                error_code="worker_shutdown",
            )
            raise
        except Exception:
            # Erros inesperados do adaptador também não comprovam ausência de geração.
            error_code = "internal_error"
        if dispatched[0].status == "prepared":
            with self.store.transaction(actor="worker", source="task_preflight_error") as uow:
                uow.execution.assert_claim(claim, now=utc_now())
                uow.execution.charge_active(
                    claim, math.ceil((time.monotonic() - start) * 1000), now=utc_now()
                )
                if error_code == "global_stop":
                    self._settle_stopped(uow, claim, dispatched[0])
                elif error_code == "policy_approval_required":
                    task = uow.tasks.get(claim.task.id)
                    if (
                        task.desired_state != "running"
                        or task.control_revision != dispatched[0].task_control_revision
                    ):
                        self._settle_conflict(uow, claim, dispatched[0], 0)
                    else:
                        try:
                            decision = self.providers.model_policy(uow, prepared)
                        except ProviderError:
                            self._settle_local(uow, claim, code="context_changed")
                        else:
                            if decision.effect == "ask":
                                self._wait_for_approval(uow, claim, prepared, decision)
                            else:
                                # Uma mudança depois da guarda não dispara retry.
                                self._settle_local(uow, claim, code="policy_denied")
                else:
                    self._settle_local(uow, claim, code=error_code or "internal_error")
            return True
        self._finish(
            claim,
            dispatched[0],
            prepared,
            elapsed_ms=math.ceil((time.monotonic() - start) * 1000),
            response=response,
            error_code=error_code,
            usage=usage,
            released=released,
        )
        return True

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        while stop_event is None or not stop_event.is_set():
            worked = await self.run_once()
            if not worked:
                if stop_event is None:
                    await asyncio.sleep(self.poll_seconds)
                else:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
                    except TimeoutError:
                        pass
