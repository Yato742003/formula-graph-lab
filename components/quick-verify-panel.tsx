'use client';

import {useRef, useState} from 'react';
import {renderFormulaHtml} from '@/lib/graph-formula-render';

type Verdict = {outcome: string; scope?: string; error_code?: string; reason_code?: string;
  missing_symbols?: string[]; duration_ms?: number; witness?: unknown; counterexample?: unknown};

export default function QuickVerifyPanel({equation, onReview}: {equation:string; onReview:()=>void}) {
  const [formula, setFormula] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Verdict | null>(null);
  const retryKey = useRef('');
  async function verify() {
    setBusy(true);
    if (!retryKey.current) retryKey.current = crypto.randomUUID();
    try {
      const response = await fetch('/api/checks/symbolic', {method:'POST',
        headers:{'content-type':'application/json','idempotency-key':retryKey.current},
        body: JSON.stringify({target_uuid:equation, formula_b:formula})});
      if (!response.ok) throw new Error();
      const data = await response.json() as Verdict;
      if (data.scope !== 'ad_hoc_check_not_frozen_campaign') throw new Error();
      setResult(data);
      retryKey.current = '';
    } catch {setResult({outcome:'unavailable'});}
    finally {setBusy(false);}
  }
  const reason = result?.reason_code ?? result?.error_code;
  const preview = renderFormulaHtml(formula);
  return <section className="inspector-section inspector-contracts">
    <h3>Quick Verify</h3><small>ad-hoc check · not frozen campaign</small>
    <label>Proposed rewrite<textarea value={formula} maxLength={2048} disabled={busy} onChange={e => {
      setFormula(e.target.value); setResult(null); retryKey.current = '';
    }}/></label>
    {formula && preview && <div className="inspector-latex-math" dangerouslySetInnerHTML={{__html:preview}}/>}
    <button type="button" disabled={busy || !formula.trim()} onClick={() => void verify()}>
      {busy ? 'Checking…' : 'Verify in Sandbox'}</button>
    {result && <div aria-live="polite"><strong>{result.outcome}</strong>
      {result.duration_ms !== undefined && <span> · {result.duration_ms} ms</span>}
      {reason && <p>{reason} {result.missing_symbols?.join(', ')}</p>}
      {result.witness != null && <pre>{JSON.stringify(result.witness, null, 2)}</pre>}
      {result.counterexample != null && <pre>{JSON.stringify(result.counterexample, null, 2)}</pre>}
      {reason?.startsWith('UNRESOLVED_') && <button type="button" onClick={onReview}>Open Batch Review</button>}
    </div>}
  </section>;
}
