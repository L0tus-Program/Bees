import { Activity, Bell, Brain, CalendarClock, Cpu, FileCheck, Fingerprint, ListTodo, Monitor, Plug, Radio, ShieldCheck, Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { SETTINGS_VIEWS } from './settingsViews'
import type { SettingsView } from './settingsViews'

const icons: Record<SettingsView, LucideIcon> = {
  profile: Fingerprint, model: Cpu, memories: Brain, deliveries: FileCheck, tasks: ListTodo,
  skills: Sparkles, routines: CalendarClock, policies: ShieldCheck, tools: Plug,
  computer: Monitor, channels: Radio, attention: Bell, usage: Activity,
}
const groups: { title: 'settingsBee' | 'settingsWork' | 'settingsAccess'; views: SettingsView[] }[] = [
  { title: 'settingsBee', views: ['profile', 'model', 'memories'] },
  { title: 'settingsWork', views: ['deliveries', 'tasks', 'skills', 'routines'] },
  { title: 'settingsAccess', views: ['policies', 'tools', 'computer', 'channels', 'attention', 'usage'] },
]

export function SettingsNavigation({ view, label, onSelect }: { view: SettingsView; label: (view: SettingsView) => string; onSelect: (view: SettingsView, focusContent: boolean) => void }) {
  const { t } = useTranslation('workspace')
  return <>
    <div className="shrink-0 border-b bg-card p-3 md:hidden">
      <label className="flex flex-col gap-2 text-sm font-medium" htmlFor="settings-section">
        {t('settingsSection')}
        <select id="settings-section" value={view} onChange={(event) => onSelect(event.target.value as SettingsView, false)}
          className="min-h-11 w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
          {SETTINGS_VIEWS.map((item) => <option key={item} value={item}>{label(item)}</option>)}
        </select>
      </label>
    </div>
    <nav className="hidden min-h-0 w-56 shrink-0 flex-col gap-5 overflow-y-auto border-r bg-card/50 p-3 md:flex" aria-label={t('settings')}>
      {groups.map((group) => <div key={group.title}>
        <p className="px-3 pb-2 text-xs font-semibold text-muted-foreground">{t(group.title)}</p>
        <ul className="flex flex-col gap-1">
          {group.views.map((item) => {
            const Icon = icons[item]
            return <li key={item}><Button variant={view === item ? 'secondary' : 'ghost'} aria-current={view === item ? 'page' : undefined}
              className="h-auto min-h-10 w-full justify-start whitespace-normal py-2 text-left" onClick={() => onSelect(item, true)}>
              <Icon aria-hidden="true" /><span>{label(item)}</span>
            </Button></li>
          })}
        </ul>
      </div>)}
    </nav>
  </>
}
