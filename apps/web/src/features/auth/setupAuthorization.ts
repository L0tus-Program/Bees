type BrowserLocation = Pick<Location, 'hash' | 'pathname' | 'search'>
type BrowserHistory = Pick<History, 'replaceState'>

export function validSetupToken(value: string): boolean {
  return /^[A-Za-z0-9_-]{43}$/.test(value)
}

export function createSetupAuthorization() {
  let token: string | undefined

  return {
    capture(location: BrowserLocation, history: BrowserHistory) {
      token = undefined
      const fragment = location.hash
      if (!fragment) return
      // Initial fragments are reserved for the launcher, not application navigation.
      // Sanitize before extracting or validating. Invalid setup fragments are removed too.
      // Never put the token in history state, a query string or browser storage.
      history.replaceState(null, '', `${location.pathname}${location.search}`)
      const parameters = new URLSearchParams(fragment.replace(/^#/, ''))
      const values = parameters.getAll('setup')
      if (values.length !== 1 || [...parameters.keys()].length !== 1 || !validSetupToken(values[0])) return
      token = values[0]
    },
    available() { return token !== undefined },
    forSetup(configured: boolean, manualToken = '') {
      if (configured) { token = undefined; return undefined }
      return token ?? (validSetupToken(manualToken.trim()) ? manualToken.trim() : undefined)
    },
    clear() { token = undefined },
  }
}

export const setupAuthorization = createSetupAuthorization()

// Imported first by main.tsx. This runs before application modules and rendering.
if (typeof window !== 'undefined') setupAuthorization.capture(window.location, window.history)
