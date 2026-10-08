"""Reserva durável e handoff interno sob um único lock do host.

Não descobre jobs, inscreve credenciais ou ativa hardware por CLI. A composição
confiável fornece plano fechado, inscrição, ledger v2, kit e backend próprios.
Uma resposta perdida não repete claim, troca dono ou declara um efeito de VM.
"""

import threading
from pathlib import Path

from pydantic import ValidationError

from bees_host.provisioning.contracts import Plan, ProvisionError
from bees_host.provisioning.enrollment import EnrollmentStore
from bees_host.provisioning.http_authority import HTTPAuthority
from bees_host.provisioning.journal import Journal, private
from bees_host.provisioning.runner import Runner
from bees_host.provisioning.supervisor import Supervisor
from bees_host.provisioning.supervisor_store import SupervisorStore


class Acquisition:
    """Uma tentativa explícita por objeto; nenhum caminho de replay ou retomada."""

    def __init__(
        self,
        store: SupervisorStore,
        enrollment: EnrollmentStore,
        backend,
        template,
        journals_directory: Path,
        *,
        cancelled: threading.Event | None = None,
    ):
        self.store, self.enrollment = store, enrollment
        self.backend, self.template = backend, template
        self.journals_directory = Path(journals_directory).absolute()
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._used = False

    def _guard(self):
        if self.cancelled.is_set():
            raise ProvisionError("provision_cancelled")

    def run(self, plan: Plan):
        if self._used:
            raise ProvisionError("provision_reconciliation_required")
        self._used = True
        try:
            if type(plan) is not Plan:
                raise ValueError
            plan = Plan.model_validate_json(plan.model_dump_json())
        except ValidationError, ValueError, TypeError, AttributeError:
            raise ProvisionError("provision_plan_invalid") from None
        ticket, journal = None, None
        with self.store.lock():
            try:
                self.store.assert_acquisition_ready()
                self._guard()
                state = self.enrollment.load()
                if (self.store.installation_id, self.store.host_id) != (
                    state.installation_id,
                    state.host_id,
                ) or (plan.installation_id, plan.host_id) != (
                    state.installation_id,
                    state.host_id,
                ):
                    raise ProvisionError("provision_supervisor_binding_invalid")
                private(self.journals_directory, directory=True)
                target = self.journals_directory / str(plan.plan_id)
                if target.exists() or target.is_symlink():
                    raise ProvisionError("provision_state_already_present")
                # Sondagem e kit somente leitura antes de consumir uma lease do core.
                self.backend.preflight()
                self.template.verify(plan)
                self._guard()
                ticket = self.store.prepare_acquisition(
                    enrollment_store_id=state.store_id,
                    issue_request_id=state.issue_request_id,
                    provisioner_id=state.provisioner_id,
                    origin=state.origin,
                    plan_id=plan.plan_id,
                    plan_hash=plan.digest(),
                )
                self._guard()
                # Até resolução DNS ocorre somente depois do commit dos identificadores.
                with HTTPAuthority(
                    state.origin,
                    state.provisioner_credential,
                    installation_id=state.installation_id,
                    host_id=state.host_id,
                    provisioner_id=state.provisioner_id,
                ) as authority:
                    claim = authority.claim(
                        plan.plan_id, plan.digest(), ticket.owner_id, ticket.request_id
                    )
                    if claim.plan != plan:
                        raise ProvisionError("provision_plan_invalid")
                    self.store.accept_acquisition(ticket, claim)
                    self._guard()
                    journal = Journal.initialize(target, claim)
                    self._guard()
                    supervisor = Supervisor(
                        Runner(journal, authority, self.backend, self.template),
                        self.store,
                        cancelled=self.cancelled,
                    )
                    return supervisor._run_locked(acquisition=ticket)
            except BaseException as error:
                if ticket is not None:
                    try:
                        self.store.fail_acquisition(ticket, "unknown")
                    except Exception:
                        # Falha de persistência mantém prepared/accepted/running bloqueados.
                        pass
                    raise ProvisionError("provision_acquisition_unknown") from None
                if isinstance(error, ProvisionError):
                    raise
                raise ProvisionError("provision_acquisition_unavailable") from None
            finally:
                if journal is not None:
                    journal.close()
