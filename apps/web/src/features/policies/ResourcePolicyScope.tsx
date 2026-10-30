import { useTranslation } from 'react-i18next'
import type { PolicyScope } from '../../api/policies'
import '../approvals/resourceApprovals.css'

export function ResourcePolicyScope({ scope }: { scope: PolicyScope }) {
  const { t } = useTranslation('resourceApprovals')
  return <div className="policy-target resource-approval"><p><strong>{t('transform')}</strong> · {t(String(scope.parameters?.operation))}</p><p>{t('exact')}</p>
    <details><summary>{t('scope')}</summary><dl><dt>{t('resourceId')}</dt><dd>{scope.environment_id}</dd><dt>{t('installation')}</dt><dd>{String(scope.parameters?.installation_id)}</dd><dt>{t('identity')}</dt><dd>{t('beesUser')}</dd><dt>{t('toolVersion')}</dt><dd>{String(scope.parameters?.tool_version)}</dd><dt>{t('grantRevision')}</dt><dd>{String(scope.parameters?.grant_revision)}</dd><dt>SHA-256</dt><dd>{String(scope.parameters?.input_hash)}</dd></dl></details>
  </div>
}
