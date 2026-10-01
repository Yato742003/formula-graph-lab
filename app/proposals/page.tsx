import { requireChatGPTUser } from '../chatgpt-auth';
import ResearchStagePage from '../research-stage-page';

export const dynamic = 'force-dynamic';

export default async function ProposalsPage() {
  const user = await requireChatGPTUser('/proposals');
  return <ResearchStagePage user={{ displayName: user.displayName, email: user.email }} surface="proposals" />;
}
