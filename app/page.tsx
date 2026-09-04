import { requireChatGPTUser } from './chatgpt-auth';
import ResearchWorkspace from './research-workspace';

export const dynamic = 'force-dynamic';

export default async function Home() {
  const user = await requireChatGPTUser('/');

  return (
    <ResearchWorkspace
      user={{ displayName: user.displayName, email: user.email }}
    />
  );
}
