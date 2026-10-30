export const SETTINGS_VIEWS = ['profile', 'model', 'deliveries', 'tasks', 'memories', 'skills', 'routines', 'policies', 'tools', 'computer', 'channels', 'attention', 'usage'] as const
export type SettingsView = typeof SETTINGS_VIEWS[number]
