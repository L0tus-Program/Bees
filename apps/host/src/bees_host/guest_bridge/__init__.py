"""Ponte somente leitura experimental; não inicia listener nem concede acesso ao importar."""

from bees_host.guest_bridge.protocol import Binding, BridgeError
from bees_host.guest_bridge.session import HostSession, SessionFence
from bees_host.guest_bridge.tls import HostIdentity

__all__ = ["Binding", "BridgeError", "HostIdentity", "HostSession", "SessionFence"]
