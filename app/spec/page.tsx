import { requireChatGPTUser } from '../chatgpt-auth';
import ResearchWorkspace from '../research-workspace';

export const dynamic = 'force-dynamic';

export default async function SpecPage() {
  const user = await requireChatGPTUser('/spec');
  return <ResearchWorkspace user={{ displayName: user.displayName, email: user.email }} stage="spec" />;
}
