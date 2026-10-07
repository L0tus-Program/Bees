"""Um efeito fechado por chamada; nunca retoma efeitos desconhecidos automaticamente."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from bees_host.provisioning.backend import TIMEOUT, HyperVBackend
from bees_host.provisioning.contracts import (
    OPERATIONS,
    Claim,
    Inventory,
    Operation,
    ProvisionAuthority,
    ProvisionError,
    ReceiptResult,
    check_claim,
)
from bees_host.provisioning.image import LocalTemplate
from bees_host.provisioning.journal import Journal


def checked(
    operation: Operation, inventory: Inventory, claim: Claim, expected_vm_id: UUID | None
) -> ReceiptResult:
    plan = claim.plan
    if not inventory.found or not inventory.owned or not inventory.fixed_vhdx:
        raise ProvisionError("provision_inventory_conflict")
    if inventory.disk_bytes != plan.disk_bytes:
        raise ProvisionError("provision_inventory_conflict")
    if operation == "create_vhd":
        if inventory.vm_id is not None:
            raise ProvisionError("provision_inventory_conflict")
    else:
        if (
            not inventory.vm_id
            or not inventory.powered_off
            or not inventory.generation2
            or (expected_vm_id is not None and inventory.vm_id != expected_vm_id)
        ):
            raise ProvisionError("provision_inventory_conflict")
        if operation in {"configure_vm", "remove_nic", "attach_iso", "verify"}:
            if inventory.cpu_count != plan.cpu_count or inventory.memory_bytes != plan.memory_bytes:
                raise ProvisionError("provision_inventory_conflict")
        if operation in {"remove_nic", "attach_iso", "verify"} and inventory.network_adapter_count:
            raise ProvisionError("provision_inventory_conflict")
        if operation in {"attach_iso", "verify"} and not inventory.iso_attached:
            raise ProvisionError("provision_inventory_conflict")
    if operation == "verify":
        return ReceiptResult(
            vm_id=inventory.vm_id,
            verified=True,
            cpu_count=inventory.cpu_count,
            memory_bytes=inventory.memory_bytes,
            disk_bytes=inventory.disk_bytes,
            powered_off=True,
            network_none=True,
            image_iso_sha256=plan.image_iso_sha256,
        )
    return ReceiptResult(vm_id=inventory.vm_id, verified=True)


class Runner:
    """Authority é adaptador confiável interno, nunca arquivo/callback de conteúdo.

    Não há CLI/registro nesta entrega. Supervisor futuro deve fornecer concessão
    própria bp_, snapshot do core e raiz privada gerenciada; diagnóstico bh_ não serve.
    """

    def __init__(
        self,
        journal: Journal,
        authority: ProvisionAuthority,
        backend: HyperVBackend,
        template: LocalTemplate,
    ):
        self.journal, self.authority, self.backend, self.template = (
            journal,
            authority,
            backend,
            template,
        )

    def _vm_id(self):
        result = None
        for operation in OPERATIONS:
            row = self.journal.operations().get(operation)
            if row and row["result_json"]:
                try:
                    current = ReceiptResult.model_validate_json(row["result_json"])
                except ValidationError:
                    raise ProvisionError("provision_state_invalid") from None
                if current.vm_id is not None:
                    if result is not None and current.vm_id != result:
                        raise ProvisionError("provision_state_invalid")
                    result = current.vm_id
        return result

    def _unresolved(self):
        for operation, row in self.journal.operations().items():
            if row["status"] == "confirmed":
                continue
            if row["pid"] is not None:
                if row["start_ticks"] is None or self.backend.running(
                    row["pid"], row["start_ticks"]
                ):
                    raise ProvisionError("provision_owner_running")
            self.journal.unknown(operation)
            raise ProvisionError("provision_reconciliation_required")

    def execute_next(self) -> ReceiptResult:
        """Uma operação isolada; o supervisor mantém o lock durante a sequência inteira."""
        with self.journal.lock():
            return self._execute_locked()

    def _execute_locked(self, *, guard: Callable[[], None] | None = None) -> ReceiptResult:
        """Entrada interna do supervisor; callback confiável, nunca recebido do modelo."""
        if not self.journal._locked:
            raise ProvisionError("provision_lock_required")
        self._unresolved()
        rows = self.journal.operations()
        if len(rows) >= len(OPERATIONS):
            raise ProvisionError("provision_already_completed")
        operation = OPERATIONS[len(rows)]
        self.backend.preflight()
        iso = self.template.verify(self.journal.claim.plan)
        vm_id = self._vm_id()
        # Preflight inventário não modifica a VM. Não consumir a autorização em conflito.
        inventory = self.backend.inspect(self.journal.claim.plan, iso, vm_id)
        if operation == "create_vhd" and inventory.found:
            raise ProvisionError("provision_inventory_conflict")
        current = self.authority.assert_current(self.journal.claim)
        check_claim(current, self.journal.claim, datetime.now(UTC), remaining=TIMEOUT + 5)
        self.journal.update_claim(current)
        row = self.journal.prepare(operation)
        command = None
        try:
            self.journal.transition(operation, "prepared", "authority_started")
            permit = self.authority.begin_dispatch(current, operation, UUID(row["request_id"]))
            check_claim(permit.claim, current, datetime.now(UTC), remaining=TIMEOUT + 5)
            if (
                permit.cached
                or not permit.dispatch_allowed
                or permit.operation != operation
                or permit.status != "dispatch_started"
            ):
                raise ProvisionError("provision_dispatch_replay_refused")
            self.journal.update_claim(permit.claim)
            self.journal.transition(
                operation,
                "authority_started",
                "authorized",
                effect_request_id=permit.effect_request_id,
            )
            command = self.backend.start(
                current.plan, operation, permit.effect_request_id, iso, vm_id
            )
            pid, start_ticks = command.ready()
            self.journal.transition(
                operation, "authorized", "dispatch_started", pid=pid, start_ticks=start_ticks
            )
            # Persistência PID/start acima é obrigatória antes de qualquer GO.
            before_go = self.authority.assert_current(permit.claim)
            check_claim(before_go, permit.claim, datetime.now(UTC), remaining=TIMEOUT + 5)
            self.journal.update_claim(before_go)
            if guard is not None:
                guard()
            command.go()
            inventory = command.finish(guard=guard) if guard is not None else command.finish()
            result = checked(operation, inventory, current, vm_id)
            self.journal.transition(operation, "dispatch_started", "result_observed", result=result)
            fresh = self.authority.assert_current(permit.claim)
            check_claim(fresh, permit.claim, datetime.now(UTC))
            receipt = self.authority.record_receipt(
                fresh, permit.effect_request_id, UUID(row["publish_id"]), result
            )
            if (
                receipt.operation != operation
                or receipt.effect_request_id != permit.effect_request_id
                or receipt.status != "confirmed"
                or receipt.dispatch_allowed
            ):
                raise ProvisionError("provision_receipt_invalid")
            published = receipt.claim
            # Final pode terminar claim; identidade/snapshot ainda precisam coincidir.
            comparable = published.model_copy(update={"status": fresh.status})
            check_claim(comparable, fresh, datetime.now(UTC))
            if operation != "verify" and published.status not in {
                "claimed",
                "dispatch_started",
            }:
                raise ProvisionError("provision_claim_stale")
            if operation == "verify" and published.status != "confirmed":
                raise ProvisionError("provision_receipt_invalid")
            self.journal.update_claim(published)
            self.journal.transition(operation, "result_observed", "confirmed")
            return result
        except BaseException:
            self.journal.unknown(operation)
            raise ProvisionError("provision_operation_unknown") from None
        finally:
            if command is not None:
                command.close()

    def reconcile(self) -> Literal["matched", "notfound", "conflict"]:
        """Só leitura. Mesmo matched não limpa unknown, concede lease ou repete comando."""
        with self.journal.lock():
            for row in self.journal.operations().values():
                if row["pid"] is not None:
                    if self.backend.running(row["pid"], row["start_ticks"]):
                        raise ProvisionError("provision_owner_running")
            iso = self.template.verify(self.journal.claim.plan)
            inventory = self.backend.inspect(self.journal.claim.plan, iso, self._vm_id())
            if not inventory.found:
                return "notfound"
            return (
                "matched"
                if inventory.owned and (inventory.vm_id is None or inventory.powered_off)
                else "conflict"
            )
