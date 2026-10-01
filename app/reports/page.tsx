import { requireChatGPTUser } from '../chatgpt-auth';
import ResearchStagePage from '../research-stage-page';

export const dynamic = 'force-dynamic';

export default async function ReportsPage() {
  const user = await requireChatGPTUser('/reports');
  return <ResearchStagePage user={{ displayName: user.displayName, email: user.email }} surface="reports" />;
}
