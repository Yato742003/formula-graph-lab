import { equationUuid, inspectorRequest } from '@/lib/server/inspector-request';
import { parseContractReviewInput } from '@/lib/server/contract-review';

export function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const id = query.get('equation_uuid') ?? '';
  if (!equationUuid.test(id) || query.size !== 1) return Response.json({code: 'INVALID_REQUEST'}, {status: 400});
  return inspectorRequest(request, '/v1/research/equations/' + id + '/contracts', 'bff_equation_contracts', 0);
}

export function POST(request: Request) {
  return inspectorRequest(request, '/v1/research/contract-reviews/batch', 'bff_batch_contract_reviews', 64 * 1024, data => {
    const {equation_uuid, rationale, items} = data;
    if (Object.keys(data).length !== 3 || typeof equation_uuid !== 'string' || !equationUuid.test(equation_uuid)
        || typeof rationale !== 'string' || rationale.trim().length < 3 || rationale.length > 500
        || !Array.isArray(items) || !items.length || items.length > 32) throw new Error('Invalid batch');
    const symbols = new Set();
    for (const item of items) {
      if (Object.keys(item).length !== 2 || symbols.has(item.symbol)) throw new Error('Invalid item');
      parseContractReviewInput({equation_uuid, symbol_name: item.symbol, reviewed_contract: item.reviewed_contract,
        decision: 'accepted', evidence: [rationale]});
      symbols.add(item.symbol);
    }
    return {equation_uuid, rationale, items};
  });
}
