import { requireChatGPTUser } from '../chatgpt-auth';
import { OpsDashboard } from '@/components/ops-dashboard';
import { WorkspaceShell } from '@/components/workspace-shell';

export const dynamic = 'force-dynamic';

export default async function OpsPage() {
  const user = await requireChatGPTUser('/ops');
  return (
    <WorkspaceShell user={{ displayName: user.displayName }}>
      <OpsDashboard />
    </WorkspaceShell>
  );
}
