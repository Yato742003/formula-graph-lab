import { getChatGPTUser } from '@/app/chatgpt-auth';
import { getD1 } from '@/db';
import {
  D1GraphSnapshotRepository,
  emptyGraphSnapshot,
  GraphSnapshotWorkflowError,
  HttpGraphSnapshotClient,
} from '@/lib/server/evidence-graph';
import {
  resolveGraphApiConfiguration,
  workspaceIdentifierForUser,
} from '@/lib/server/paper-import';
import { env } from 'cloudflare:workers';

function json(body: unknown, status: number) {
  return Response.json(body, {
    status,
    headers: {
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    },
  });
}

export async function GET(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return json({ code: 'AUTH_REQUIRED' }, 401);
  if (request.headers.get('sec-fetch-site') === 'cross-site') {
    return json({ code: 'CROSS_SITE_REQUEST_REJECTED' }, 403);
  }

  const workspaceId = await workspaceIdentifierForUser(user.userId);
  let paper;
  try {
    paper = await new D1GraphSnapshotRepository(getD1()).latestOwnedPaper(
      workspaceId,
      user.userId,
    );
  } catch {
    return json({ code: 'DATABASE_UNAVAILABLE' }, 503);
  }
  if (!paper) return json(emptyGraphSnapshot(), 200);

  const graphConfiguration = resolveGraphApiConfiguration(
    {
      GRAPH_API_URL: env.GRAPH_API_URL,
      GRAPH_API_SERVICE_TOKEN: env.GRAPH_API_SERVICE_TOKEN,
    },
    env.APP_ENV !== 'development',
  );
  if (!graphConfiguration) {
    return json({ code: 'GRAPH_API_NOT_CONFIGURED' }, 503);
  }

  try {
    const graph = await new HttpGraphSnapshotClient(graphConfiguration).load(
      workspaceId,
      paper,
    );
    return json({ paper, ...graph }, 200);
  } catch (error) {
    if (error instanceof GraphSnapshotWorkflowError) {
      return json({ code: error.code }, error.status);
    }
    return json({ code: 'GRAPH_LOAD_FAILED' }, 503);
  }
}
