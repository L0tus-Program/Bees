"""Execução fechada dos contratos de ferramenta no executor.

Cada comando passa pelo journal local: replay devolve o recibo anterior; um
início interrompido nunca é repetido. Escopos pessoais (raízes, escrita, shell,
navegador) chegam com o comando e são conferidos aqui também.
"""

import logging
import os
from pathlib import Path

from bees_executor import files, office, shell
from bees_executor.files import Roots, ToolFailure
from bees_executor.journal import Journal

WRITE = {"files.write", "files.delete", "office.convert"}
BROWSER_ACTIONS = {
    "browser.open": "open",
    "browser.snapshot": "snapshot",
    "browser.click": "click",
    "browser.type": "type",
    "browser.press": "press",
    "browser.scroll": "scroll",
    "browser.back": "back",
    "browser.screenshot": "screenshot",
}


class ToolHost:
    def __init__(
        self,
        roots: dict[str, Path],
        home: Path,
        journal: Journal,
        *,
        named: bool,
        env: dict[str, str] | None = None,
        browser_profile: Path | None = None,
        screenshots: Path | None = None,
    ):
        self.configured = {alias: Path(path) for alias, path in roots.items()}
        self.home = home
        self.journal = journal
        self.named = named
        self.env = env or self.default_env(home)
        self.browser_profile = browser_profile or home / ".bees-browser"
        self.screenshots = screenshots
        self._browser = None

    @staticmethod
    def default_env(home: Path) -> dict[str, str]:
        keep = ("PATH", "LANG", "LC_ALL", "DISPLAY", "TZ", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        env = {key: os.environ[key] for key in keep if key in os.environ}
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.setdefault("LANG", "C.UTF-8")
        env["HOME"] = str(home)
        if os.name == "nt":
            env["USERPROFILE"] = str(home)
        return env

    def roots(self, scope: dict | None) -> Roots:
        if not self.named:
            return Roots(self.configured, named=False)
        allowed = set((scope or {}).get("roots") or [])
        chosen = {alias: path for alias, path in self.configured.items() if alias in allowed}
        return Roots(chosen, named=True)

    @property
    def browser(self):
        if self._browser is None:
            from bees_executor.browser import Browser

            self._browser = Browser(
                self.browser_profile,
                self.screenshots or next(iter(self.configured.values())) / "screenshots",
            )
        return self._browser

    def execute(self, command: dict) -> dict:
        command_id = command["id"]
        tool = command["tool"]
        prior = self.journal.begin(command_id, tool)
        if prior is not None:
            return prior
        try:
            result = self._run(tool, command.get("arguments") or {}, command.get("scope"))
            receipt = {"status": "confirmed", "result": result}
        except ToolFailure as failure:
            receipt = {"status": "failed", "code": failure.code, "detail": failure.detail}
        except (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError) as error:
            receipt = {"status": "failed", "code": "os_error", "detail": type(error).__name__}
        except Exception as error:  # efeito pode ter começado: nunca afirmar ausência
            logging.getLogger("bees_executor").exception("Falha inesperada em %s", tool)
            receipt = {
                "status": "unknown",
                "code": "executor_error",
                "detail": type(error).__name__,
            }
        self.journal.finish(command_id, receipt)
        return receipt

    def _run(self, tool: str, arguments: dict, scope: dict | None):
        name = tool.split(".", 1)[1] if tool.startswith(("computer.", "personal.")) else tool
        personal = tool.startswith("personal.")
        if personal:
            scope = scope or {}
            if name in WRITE and not scope.get("write"):
                raise ToolFailure("scope_denied", "Escrita não concedida.")
            if name == "shell.run" and not scope.get("shell"):
                raise ToolFailure("scope_denied", "Terminal não concedido.")
            if name.startswith("browser.") and not scope.get("browser"):
                raise ToolFailure("scope_denied", "Navegador não concedido.")
        if name == "files.list":
            return files.list_files(self.roots(scope), arguments.get("path", "."))
        if name == "files.read":
            return files.read_file(
                self.roots(scope),
                arguments["path"],
                arguments.get("offset", 0),
                arguments.get("max_chars", 20000),
            )
        if name == "files.write":
            return files.write_file(
                self.roots(scope), arguments["path"], arguments["content"], arguments.get("append")
            )
        if name == "files.delete":
            return files.delete_file(self.roots(scope), arguments["path"])
        if name == "files.publish":
            return files.publish_file(self.roots(scope), arguments["path"], arguments.get("name"))
        if name == "shell.run":
            roots = self.roots(scope)
            cwd = roots.resolve(arguments.get("cwd", "."), must_exist=True)
            if not cwd.is_dir():
                raise ToolFailure("not_a_directory")
            return shell.run(
                arguments["command"], cwd, arguments.get("timeout_seconds", 120), self.env
            )
        if name == "office.convert":
            roots = self.roots(scope)
            source = roots.resolve(arguments["path"], must_exist=True)
            produced = office.convert(source, arguments["format"], self.env)
            return {"path": roots.display(produced), "size": produced.stat().st_size}
        if name in BROWSER_ACTIONS:
            result = self.browser.call(BROWSER_ACTIONS[name], arguments)
            if isinstance(result.get("path"), Path):
                roots = Roots(self.configured, named=self.named)
                try:
                    result["path"] = roots.display(result["path"])
                except Exception:
                    result["path"] = result["path"].name
            return result
        raise ToolFailure("tool_unavailable")

    def close(self):
        if self._browser is not None:
            self._browser.close()
