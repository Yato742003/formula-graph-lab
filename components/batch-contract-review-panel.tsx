'use client';

import { useEffect, useRef, useState } from 'react';
import type { ReviewedContractInput } from '@/lib/server/contract-review';

type Entry = {
  symbol: string;
  inferred: ReviewedContractInput & { inference_confidence: number };
  latest_review: {decision: string; reviewed_contract: ReviewedContractInput | null} | null;
};
type Row = {symbol: string; value: ReviewedContractInput; shape: string; selected: boolean;
  approved: boolean; confidence: number};
const categories = ['scalar', 'vector', 'matrix', 'tensor', 'function', 'distribution', 'index'];
const domains = ['real', 'positive', 'non_negative', 'complex', 'integer'];

export default function BatchContractReviewPanel({equation}: {equation: string}) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [rationale, setRationale] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, refresh] = useState(0);
  const previous = useRef({body: '', key: ''});
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch('/api/contract-reviews/batch?equation_uuid=' + equation,
          {signal: controller.signal});
        if (!response.ok) throw new Error();
        const data = await response.json() as {items: Entry[]};
        const next = data.items.map(({symbol, inferred, latest_review}) => {
          const approved = latest_review?.decision === 'accepted';
          const value = approved && latest_review.reviewed_contract ? latest_review.reviewed_contract : {
            name: symbol, category: inferred.category, domain: inferred.domain, shape: inferred.shape,
            constraints: inferred.constraints, ...(inferred.scope ? {scope: inferred.scope} : {}),
          };
          return {symbol, value, shape: value.shape?.join(', ') ?? '', selected: false,
            approved, confidence: inferred.inference_confidence};
        });
        if (!controller.signal.aborted) { setRows(next); setNotice(next.length ? '' : 'No symbols in this equation.'); }
      } catch { if (!controller.signal.aborted) {setRows([]); setNotice('Could not load symbol contracts.');} }
    }
    void load();
    return () => controller.abort();
  }, [equation, open, revision]);

  const count = rows.filter(row => row.selected).length;
  function update(index: number, changes: Partial<Row>) {
    setRows(current => current.map((row, i) => i === index ? {...row, ...changes} : row));
  }
  function preset(name: string) {
    setRows(current => current.map(row => {
      const attention = ['Q','K','V'].includes(row.symbol);
      if (name === 'Attention Q/K/V' && !attention && row.symbol !== 'd_k') return row;
      const matrix = name === 'Attention Q/K/V' && attention;
      const vector = name === 'Probability weights';
      return {...row, selected: true, approved: false, shape: matrix ? 'n, d' : vector ? 'n' : '',
        value: {name: row.symbol, category: matrix ? 'matrix' : vector ? 'vector' : 'scalar',
          domain: vector ? 'non_negative' : name === 'Positive scalars' || (name === 'Attention Q/K/V' && !matrix) ? 'positive' : 'real',
          constraints: row.value.constraints, shape: null}};
    }));
  }
  async function approve() {
    setBusy(true);
    try {
      const items = rows.filter(row => row.selected).map(row => ({
        symbol: row.symbol, reviewed_contract: {...row.value, shape: row.shape.trim()
          ? row.shape.split(',').map(s => /^\d+$/.test(s.trim()) ? Number(s.trim()) : s.trim())
          : row.value.category === 'scalar' ? [] : null},
      }));
      const body = JSON.stringify({equation_uuid: equation, rationale: rationale.trim(), items});
      if (previous.current.body !== body) previous.current = {body, key: crypto.randomUUID()};
      const response = await fetch('/api/contract-reviews/batch', {method: 'POST', body,
        headers: {'content-type': 'application/json', 'idempotency-key': previous.current.key}});
      if (!response.ok) throw new Error();
      previous.current.body = '';
      setRows([]);
      refresh(n => n + 1);
    } catch { setNotice('Approval failed. Review the values and retry.'); }
    finally { setBusy(false); }
  }
  return <section id="batch-contract-review" className="inspector-section inspector-contracts">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Batch Review</button>
    {open && <fieldset disabled={busy}>
      <legend>Symbol contracts</legend>
      <div>{['Scalar reals','Positive scalars','Attention Q/K/V','Probability weights'].map(name =>
        <button type="button" key={name} onClick={() => preset(name)}>{name}</button>)}</div>
      {rows.map((row, i) => <fieldset key={row.symbol}><legend>
        <label><input type="checkbox" checked={row.selected} onChange={e => update(i, {selected: e.target.checked})}/>
          {row.symbol}</label> · {row.approved ? 'Reviewed' : 'Draft · ' + row.confidence.toFixed(2)}</legend>
        {(['category','domain'] as const).map(field => <label key={field}>{field}
          <select value={row.value[field]} onChange={e => update(i, {approved: false,
            value: {...row.value, [field]: e.target.value}})}>
            {Array.from(new Set([row.value[field], ...(field === 'category' ? categories : domains)])).map(v =>
              <option key={v}>{v}</option>)}</select></label>)}
        <label>Shape<input value={row.shape} onChange={e => update(i, {approved: false, shape: e.target.value})}/></label>
      </fieldset>)}
      <label>Rationale<input value={rationale} maxLength={500} onChange={e => setRationale(e.target.value)}/></label>
      <button type="button" disabled={!count || count > 32 || rationale.trim().length < 3}
        onClick={() => void approve()}>Approve {count} reviewed contracts</button>
      <output>{notice}</output>
    </fieldset>}
  </section>;
}
