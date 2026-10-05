export function createRequestGate() {
  let active: AbortController | undefined
  return {
    cancel() { active?.abort(); active = undefined },
    begin() { active?.abort(); const controller = new AbortController(); active = controller; return controller },
    current(controller: AbortController) { return active === controller && !controller.signal.aborted },
  }
}
