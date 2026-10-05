export function startTaskPolling<T>(load: (signal: AbortSignal) => Promise<T>, accept: (value: T) => void, reject: (failure: unknown) => void, visible: () => boolean) {
  let active = true
  let controller: AbortController | undefined
  let timeout: ReturnType<typeof setTimeout> | undefined
  let generation = 0
  function schedule() { timeout = setTimeout(() => { if (visible()) void poll(); else schedule() }, 5000) }
  async function poll() {
    if (!active) return
    clearTimeout(timeout); controller?.abort()
    const version = ++generation
    controller = new AbortController()
    const signal = controller.signal
    const current = () => active && version === generation && !signal.aborted
    try { const value = await load(signal); if (current()) accept(value) }
    catch (failure) { if (current()) reject(failure) }
    finally { if (current()) schedule() }
  }
  void poll()
  return {
    refresh() { void poll() },
    stop() { active = false; ++generation; controller?.abort(); clearTimeout(timeout) },
  }
}
