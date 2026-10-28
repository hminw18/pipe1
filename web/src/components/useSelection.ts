import { useMatch } from 'react-router-dom'

import { useBusiness, useReport } from '../api/queries'

/** Current Project / Business / Report from the route, resolving parents from the child. */
export function useSelection() {
  const projectMatch = useMatch('/projects/:projectId/*')
  const businessMatch = useMatch('/businesses/:businessId/*')
  const reportMatch = useMatch('/reports/:reportId/*')
  const report = useReport(reportMatch?.params.reportId)
  const businessId = businessMatch?.params.businessId ?? report.data?.business_id
  const business = useBusiness(businessId)
  const projectId = projectMatch?.params.projectId ?? business.data?.project_id ?? report.data?.project_id
  return { projectId, businessId, reportId: reportMatch?.params.reportId }
}
