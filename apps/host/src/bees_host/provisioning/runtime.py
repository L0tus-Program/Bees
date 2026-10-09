"""Composição operacional interna fixa, acionada somente por plano explícito.

Não há CLI, emissão de credencial, descoberta de jobs, instalação de componentes
Windows, UAC ou recuperação de tentativas. Abrir consulta só estado local; run
mantém root/assets/workspace/ledger/journal travados até fechar comandos próprios.
"""

import threading

from pydantic import ValidationError

from bees_host.provisioning.acquisition import Acquisition
from bees_host.provisioning.assets import AssetsBinding, AssetsStore, WorkspaceStore
from bees_host.provisioning.backend import HyperVBackend
from bees_host.provisioning.contracts import Plan, ProvisionError
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.image import LocalTemplate
from bees_host.provisioning.root_store import RootStore
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.security import Cipher


def _plan(value):
    try:
        if type(value) is not Plan:
            raise ValueError
        return Plan.model_validate_json(value.model_dump_json())
    except ValidationError, ValueError, TypeError, AttributeError:
        raise ProvisionError("provision_plan_invalid") from None


def _cancel(value):
    if value is not None and type(value) is not threading.Event:
        raise ProvisionError("provision_cancel_invalid")
    return value if value is not None else threading.Event()


class ProvisionerRuntime:
    """Fronteira confiável fechada: nenhum caminho ou executor é argumento público."""

    def __init__(self):
        raise ProvisionError("provision_runtime_explicit_required")

    @classmethod
    def initialize_assets(cls, *, binding: EnrollmentBinding, cipher: Cipher | None = None):
        """Preparação explícita e separada: somente raiz sem qualquer tentativa anterior.

        Cria apenas assets/kit vazio. Workspace VMMS exige preparação humana própria.
        Abrir ou executar nunca chama este método.
        """
        root = RootStore.open(binding=binding, cipher=cipher)
        with root.lock() as check:
            state = root._directory / "state"
            with SupervisorStore.open_execution_locked(state / "ledger") as ledger:
                check = root._check_locked(ledger=ledger)
                if ledger.plan_inventory():
                    raise ProvisionError("provision_runtime_virgin_required")
                return AssetsStore.initialize(binding=AssetsBinding.from_root_check(check))

    @classmethod
    def open(cls, *, binding: EnrollmentBinding, cipher: Cipher | None = None):
        root = RootStore.open(binding=binding, cipher=cipher)
        with root.lock() as check:
            resource_binding = AssetsBinding.from_root_check(check)
            assets = AssetsStore.open(binding=resource_binding)
            with assets.lock():
                workspace = WorkspaceStore.open(binding=resource_binding)
                with workspace.lock():
                    assets.check_only()
                    workspace.check_only()
                    reader = SupervisorStore.open_read_only(root._directory / "state/ledger")
                    try:
                        with reader.lock():
                            root._check_locked(ledger=reader)
                            cls._workspace_plans_locked(workspace, reader)
                    finally:
                        reader.close()
        runtime = cls.__new__(cls)
        runtime._root = root
        runtime._mutex = threading.Lock()
        return runtime

    def run(self, plan: Plan, *, cancelled: threading.Event | None = None):
        plan, cancelled = _plan(plan), _cancel(cancelled)
        if not self._mutex.acquire(blocking=False):
            raise ProvisionError("provision_owner_running")
        try:
            with self._root.lock() as check:
                self._ready(check, plan, cancelled)
                resource_binding = AssetsBinding.from_root_check(check)
                assets = AssetsStore.open(binding=resource_binding)
                with assets.lock():
                    workspace = WorkspaceStore.open(binding=resource_binding)
                    with workspace.lock():
                        state = self._root._directory / "state"
                        with SupervisorStore.open_execution_locked(state / "ledger") as ledger:
                            check = self._root._check_locked(ledger=ledger)
                            self._ready(check, plan, cancelled)
                            if str(plan.plan_id) in ledger.plan_inventory():
                                raise ProvisionError("provision_reconciliation_required")
                            asset_check = assets.check_only()
                            workspace.check_only()
                            self._workspace_plans_locked(workspace, ledger)
                            if not asset_check.kit_present_local:
                                raise ProvisionError("provision_runtime_kit_required")
                            enrollment = EnrollmentStore.open(
                                state / "enrollment",
                                binding=self._root.binding,
                                cipher=self._root.cipher,
                            )
                            if enrollment.check_only().store_id != check.enrollment_store_id:
                                raise ProvisionError("provision_root_invalid")
                            backend = HyperVBackend(workspace.storage_root)
                            template = LocalTemplate(assets.kit_directory)
                            acquisition = Acquisition(
                                ledger,
                                enrollment,
                                backend,
                                template,
                                state / "plans",
                                cancelled=cancelled,
                            )
                            try:
                                result = acquisition._run_locked(plan)
                            finally:
                                # Nenhum novo efeito: mantém os locks durante as guardas
                                # finais, após fechamento dos comandos e do journal.
                                self._root._check_locked(ledger=ledger)
                                assets.check_only()
                                workspace.check_only()
                                self._workspace_plans_locked(workspace, ledger)
                            return result
        finally:
            self._mutex.release()

    def _ready(self, check, plan, cancelled):
        if check.execution_blocked_local:
            raise ProvisionError("provision_reconciliation_required")
        if (plan.installation_id, plan.host_id) != (check.installation_id, check.host_id):
            raise ProvisionError("provision_runtime_binding_invalid")
        if cancelled.is_set():
            raise ProvisionError("provision_cancelled")

    @staticmethod
    def _workspace_plans_locked(workspace, ledger):
        workspace.assert_locked()
        ledger.assert_locked()
        inventory = ledger.plan_inventory()
        allowed = {
            name for name, association in inventory.items() if association["run"] is not None
        }
        if {str(value) for value in workspace.plan_ids()} - allowed:
            raise ProvisionError("provision_workspace_orphan")
