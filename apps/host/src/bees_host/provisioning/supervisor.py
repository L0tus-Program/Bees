"""Composição interna exclusiva; não inscreve credenciais nem ativa hardware por CLI."""

import threading
import time
from datetime import UTC, datetime
from uuid import UUID

from bees_host.provisioning.backend import TIMEOUT
from bees_host.provisioning.contracts import Claim, ProvisionError, check_claim
from bees_host.provisioning.runner import Runner
from bees_host.provisioning.supervisor_store import SupervisorStore

# Não amplia os limites do core nem o prazo do comando nativo.
MAX_SECONDS = 300
CHECK_INTERVAL = 5
RENEW_MARGIN = TIMEOUT + 15


class _Authority:
    """Apenas o supervisor confiável possui esta ponte; conteúdo não recebe callbacks."""

    def __init__(self, supervisor):
        self.supervisor = supervisor

    def assert_current(self, claim):
        return self.supervisor._refresh(claim)

    def begin_dispatch(self, claim, operation, request_id):
        self.supervisor._budget()
        permit = self.supervisor.authority.begin_dispatch(claim, operation, request_id)
        self.supervisor._budget()
        return permit

    def record_receipt(self, claim, effect_id, request_id, result):
        self.supervisor._budget()
        receipt = self.supervisor.authority.record_receipt(claim, effect_id, request_id, result)
        self.supervisor._budget()
        return receipt


class Supervisor:
    """Uma corrida por claim; crash/perda/unknown exigem reconciliação humana separada.

    O ledger do host e o journal do plano ficam travados durante toda a sequência.
    A autoridade original precisa implementar renew e mark_unknown, como HTTPAuthority.
    Os caminhos/bindings são fornecidos pela composição confiável, não pelo modelo.
    """

    def __init__(
        self, runner: Runner, store: SupervisorStore, *, cancelled: threading.Event | None = None
    ):
        self.runner, self.store = runner, store
        self.authority = runner.authority
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._deadline = None
        self._next_check = None
        self._used = False

    def _budget(self):
        if self.cancelled.is_set():
            raise ProvisionError("provision_cancelled")
        if self._deadline is None or time.monotonic() >= self._deadline:
            raise ProvisionError("provision_supervisor_timeout")

    def _refresh(self, expected: Claim) -> Claim:
        self._budget()
        journal = self.runner.journal
        current = self.authority.assert_current(journal.claim)
        self._budget()
        check_claim(current, expected, datetime.now(UTC))
        check_claim(current, journal.claim, datetime.now(UTC))
        # Já confirmar a leitura evita voltar a uma revisão anterior depois de falha.
        journal.update_claim(current)
        if (current.lease_expires_at - datetime.now(UTC)).total_seconds() <= RENEW_MARGIN:
            request_id = self.store.request(current.claim_id, "renew")
            current = self.authority.renew(current, request_id)
            self._budget()
            check_claim(current, journal.claim, datetime.now(UTC), remaining=TIMEOUT + 5)
            if current.lease_expires_at < journal.claim.lease_expires_at:
                raise ProvisionError("provision_claim_stale")
            self.store.confirm(request_id, current)
            journal.update_claim(current)
        self._next_check = time.monotonic() + CHECK_INTERVAL
        return current

    def _tick(self):
        # Executado no thread dono, enquanto Command.finish espera saída/encerramento.
        self._budget()
        if self._next_check is None or time.monotonic() >= self._next_check:
            self._refresh(self.runner.journal.claim)

    def _report_unknown(self, rows):
        """Um pedido durável, sem retries. Não prova quiescência nem concede nova lease."""
        journal = self.runner.journal
        for row in rows.values():
            if row["status"] == "confirmed" or row["status"] == "prepared":
                continue
            # /begin usa o UUID durável do pedido como UUID do intent. Também permite
            # reportar a perda da resposta depois de o core já ter gravado o intent.
            effect_id = UUID(row["effect_request_id"] or row["request_id"])
            request_id = self.store.request(journal.claim.claim_id, "unknown")
            unknown = self.authority.mark_unknown(journal.claim, effect_id, request_id)
            self.store.confirm(request_id, unknown)
            journal.update_claim(unknown)
            return

    def run(self):
        if self._used:
            raise ProvisionError("provision_reconciliation_required")
        self._used = True
        self._deadline = time.monotonic() + MAX_SECONDS
        journal = self.runner.journal
        with self.store.lock(), journal.lock():
            self.store.begin(journal.claim)
            managed = Runner(journal, _Authority(self), self.runner.backend, self.runner.template)
            try:
                if journal.operations():
                    raise ProvisionError("provision_reconciliation_required")
                self._budget()
                result = None
                # Uma sequência fechada, sem busca de jobs, loops LLM ou troca de host.
                from bees_host.provisioning.contracts import OPERATIONS

                for _ in OPERATIONS:
                    self._budget()
                    result = managed._execute_locked(guard=self._tick)
                self.store.finish(journal.claim.claim_id, "completed")
                return result
            except BaseException:
                rows = journal.operations()
                for operation, row in rows.items():
                    if row["status"] != "confirmed":
                        journal.unknown(operation)
                # Quarentena local é confirmada ANTES de tentar o aviso pela rede.
                self.store.finish(journal.claim.claim_id, "unknown" if rows else "stopped")
                try:
                    self._report_unknown(rows)
                except Exception:
                    # Credencial revogada, rede perdida ou receipt já confirmado podem
                    # impedir o aviso. O journal/ledger continuam bloqueando repetição.
                    pass
                raise ProvisionError(
                    "provision_operation_unknown" if rows else "provision_supervisor_stopped"
                ) from None

    def reconcile(self):
        """Só leitura; não altera a quarentena, o dono anterior ou qualquer concessão."""
        with self.store.lock():
            # Valida o binding mesmo se não existe corrida; não cria um registro.
            if (self.store.installation_id, self.store.host_id) != (
                self.runner.journal.claim.installation_id,
                self.runner.journal.claim.host_id,
            ):
                raise ProvisionError("provision_claim_stale")
            return self.runner.reconcile()
