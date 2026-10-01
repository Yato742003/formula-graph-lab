import { requireChatGPTUser } from '../chatgpt-auth';
import { HelpCenter } from '@/components/research-help';
import { WorkspaceShell } from '@/components/workspace-shell';

export const dynamic = 'force-dynamic';

export default async function HelpPage() {
  const user = await requireChatGPTUser('/help');
  return (
    <WorkspaceShell user={{ displayName: user.displayName }}>
      <HelpCenter />
    </WorkspaceShell>
  );
}
