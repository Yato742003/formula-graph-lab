import { handleImport } from '@/lib/server/import-handler';

export function POST(request: Request) {
  return handleImport(request, false);
}
