"""Navegador próprio com perfil persistente, snapshots numerados e ações por referência.

Usa Playwright com contexto persistente (sessões sobrevivem a reinícios). As
referências valem para o último snapshot; uma página nova exige outro snapshot.
"""

import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from bees_executor.files import ToolFailure

SNAPSHOT_SCRIPT = r"""
() => {
  const selectors = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],' +
    '[role=checkbox],[role=tab],[role=menuitem],[contenteditable=true],[onclick]';
  document.querySelectorAll('[data-bees-ref]').forEach((node) => node.removeAttribute('data-bees-ref'));
  const items = [];
  let ref = 0;
  for (const node of document.querySelectorAll(selectors)) {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    if (rect.width < 1 || rect.height < 1 || style.visibility === 'hidden' || style.display === 'none') continue;
    if (node.disabled) continue;
    ref += 1;
    node.setAttribute('data-bees-ref', String(ref));
    const tag = node.tagName.toLowerCase();
    const type = (node.getAttribute('type') || '').toLowerCase();
    const label = (node.getAttribute('aria-label') || node.innerText || node.getAttribute('placeholder')
      || node.getAttribute('title') || node.getAttribute('alt') || node.getAttribute('name') || '').trim();
    const item = { ref, tag, name: label.replace(/\s+/g, ' ').slice(0, 120) };
    if (type) item.type = type;
    if (tag === 'a') item.href = node.href;
    if (['input', 'textarea', 'select'].includes(tag) && type !== 'password') item.value = String(node.value || '').slice(0, 120);
    if (type === 'password') item.sensitive = true;
    if (node.checked !== undefined && ['checkbox', 'radio'].includes(type)) item.checked = node.checked;
    items.push(item);
    if (items.length >= 300) break;
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return { title: document.title, url: location.href, text: text.slice(0, 20000), elements: items,
    text_truncated: text.length > 20000 };
}
"""


def release_profile(profile: Path) -> None:
    """Libera o perfil antes de abrir o navegador da abelha.

    Ao parar o container, o Chromium não fecha limpo e deixa ``SingletonLock`` com o
    hostname antigo; no container seguinte (outro hostname) ele recusa o perfil e sai com
    código 21. Também encerra Chromium órfão (mesmo usuário) preso a este perfil.
    """
    marker = f"--user-data-dir={profile}".encode()
    uid = os.getuid()
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else ():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != uid or marker not in (entry / "cmdline").read_bytes():
                continue
            os.kill(int(entry.name), signal.SIGKILL)
        except (OSError, ValueError):
            continue
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass


class Browser:
    def __init__(self, profile: Path, screenshots: Path, *, headless: bool | None = None):
        self.profile = profile
        self.screenshots = screenshots
        self.headless = headless if headless is not None else not os.environ.get("DISPLAY")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bees-browser")
        self.lock = threading.Lock()
        self._playwright = None
        self._context = None
        self._closed = False

    def call(self, name: str, arguments: dict, timeout: float = 60) -> dict:
        return self.pool.submit(self._dispatch, name, arguments).result(timeout=timeout)

    def _reset(self) -> None:
        """Navegador fechado (janela fechada, processo morto): a próxima ação abre outro."""
        for stop in (
            getattr(self._context, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            try:
                if stop:
                    stop()
            except Exception:
                pass
        self._context = self._playwright = None

    def _page(self):
        if self._context is not None and not self._closed:
            try:
                # Checagem barata: navegador morto (janela fechada) é aberto de novo.
                if self._context.pages:
                    self._context.pages[-1].evaluate("1")
            except Exception:
                self._closed = True
        if self._context is not None and self._closed:
            self._reset()
        if self._context is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                raise ToolFailure("browser_unavailable", "Playwright não instalado.") from None
            self.profile.mkdir(parents=True, exist_ok=True)
            release_profile(self.profile)
            self._playwright = sync_playwright().start()
            options = {
                "headless": self.headless,
                "viewport": {"width": 1280, "height": 800},
                "accept_downloads": True,
                "args": ["--no-first-run", "--no-default-browser-check"],
            }
            executable = os.environ.get("BEES_BROWSER_EXECUTABLE")
            if executable:
                options["executable_path"] = executable
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self.profile), **options
                )
            except Exception as error:
                # Não abriu: nenhuma ação aconteceu, então é falha (não resultado desconhecido).
                self._reset()
                raise ToolFailure("browser_unavailable", str(error)[:300]) from None
            self._closed = False
            self._context.on("close", lambda *_: setattr(self, "_closed", True))
            self._context.set_default_timeout(20000)
        pages = [page for page in self._context.pages if not page.is_closed()]
        return pages[-1] if pages else self._context.new_page()

    def _snapshot(self, page) -> dict:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        snapshot = page.evaluate(SNAPSHOT_SCRIPT)
        snapshot["tabs"] = len(self._context.pages)
        return snapshot

    def _element(self, page, ref: int):
        locator = page.locator(f'[data-bees-ref="{int(ref)}"]')
        if locator.count() != 1:
            raise ToolFailure("element_not_found", "Faça um novo snapshot da página.")
        return locator

    def _dispatch(self, name: str, arguments: dict) -> dict:
        page = self._page()
        if name == "open":
            url = arguments["url"]
            if urlsplit(url).scheme not in ("http", "https"):
                raise ToolFailure("unsupported_url")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as error:
                raise ToolFailure("navigation_failed", str(error)[:300]) from None
            return self._snapshot(page)
        if name == "snapshot":
            return self._snapshot(page)
        if name == "click":
            element = self._element(page, arguments["ref"])
            before = len(self._context.pages)
            element.click(timeout=15000)
            time.sleep(0.8)
            if len(self._context.pages) > before:
                page = self._context.pages[-1]
            return self._snapshot(page)
        if name == "type":
            element = self._element(page, arguments["ref"])
            if (element.get_attribute("type") or "").lower() == "password":
                raise ToolFailure(
                    "sensitive_field", "Campos de senha exigem que o usuário assuma o controle."
                )
            element.fill(arguments["text"], timeout=15000)
            if arguments.get("submit"):
                element.press("Enter")
                time.sleep(1.0)
            return self._snapshot(page)
        if name == "press":
            page.keyboard.press(arguments["key"])
            time.sleep(0.5)
            return self._snapshot(page)
        if name == "scroll":
            page.mouse.wheel(0, -700 if arguments.get("direction") == "up" else 700)
            time.sleep(0.3)
            return self._snapshot(page)
        if name == "back":
            page.go_back(wait_until="domcontentloaded")
            return self._snapshot(page)
        if name == "screenshot":
            self.screenshots.mkdir(parents=True, exist_ok=True)
            target = self.screenshots / f"captura-{time.strftime('%Y%m%d-%H%M%S')}.png"
            page.screenshot(path=str(target), full_page=False)
            return {"path": target, "url": page.url, "title": page.title()}
        raise ToolFailure("tool_unavailable")

    def close(self):
        def stop():
            if self._context is not None:
                self._context.close()
            if self._playwright is not None:
                self._playwright.stop()

        try:
            self.pool.submit(stop).result(timeout=20)
        except Exception:
            pass
        self.pool.shutdown(wait=False)
