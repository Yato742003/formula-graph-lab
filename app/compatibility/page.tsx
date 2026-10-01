import { requireChatGPTUser } from '../chatgpt-auth';
import ResearchWorkspace from '../research-workspace';

export const dynamic = 'force-dynamic';

export default async function CompatibilityPage() {
  const user = await requireChatGPTUser('/compatibility');
  return <ResearchWorkspace user={{ displayName: user.displayName, email: user.email }} stage="compatibility" />;
}
