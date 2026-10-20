export type Appearance = 'system' | 'light' | 'dark'
export const appearanceKey = 'bees:appearance:v1'
export const appearanceQuery = '(prefers-color-scheme: dark)'
export function isAppearance(value: unknown): value is Appearance { return value === 'system' || value === 'light' || value === 'dark' }
export function readAppearance(): Appearance {
  try { const value = localStorage.getItem(appearanceKey); return isAppearance(value) ? value : 'system' } catch { return 'system' }
}
export function applyAppearance(value: Appearance) {
  const theme = value === 'system' ? (window.matchMedia?.(appearanceQuery).matches ? 'dark' : 'light') : value
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}
