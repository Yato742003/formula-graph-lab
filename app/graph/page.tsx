import { requireChatGPTUser } from '../chatgpt-auth';
import ResearchWorkspace from '../research-workspace';

export const dynamic = 'force-dynamic';

export default async function GraphPage() {
  const user = await requireChatGPTUser('/graph');
  return <ResearchWorkspace user={{ displayName: user.displayName, email: user.email }} stage="graph" />;
}
