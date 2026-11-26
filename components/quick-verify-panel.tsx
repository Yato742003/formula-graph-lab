'use client';

import { useRef, useState } from 'react';
import { renderFormulaHtml } from '@/lib/graph-formula-render';

type DomainObligationItem = {
  obligation_id: string;
  predicate: string;
  expression: string;
  location: string;
  status: string;
  discharged_by?: string[];
  conditions?: string[];
};

type ChainStepVerdict = {
  step_index: number;
  from_formula: string;
  to_formula: string;
  outcome: string;
  error_code?: string | null;
  domain_status?: string;
  duration_ms: number;
  witness?: unknown;
  counterexample?: unknown;
};

type BinderAuditRow = {
  name: string;
  type_expr: string;
  bin: 'SOURCE' | 'STANDING' | 'TYPING' | 'RULED' | 'EXCESS';
  citation: string;
};

type LeanAuditReport = {
  declaration_fidelity: 'PASS' | 'FAIL';
  draft_allowlist: 'PASS' | 'FAIL';
  proof_status: 'FROZEN_UNPROVED' | 'PROVED' | 'AUDIT_FAILED';
  kernel_checked: boolean;
  sorry_count: number;
  binder_table: BinderAuditRow[];
  bin_counts: Record<string, number>;
  failure_reasons: string[];
};

type LeanExport = {
  status: 'audited_draft' | 'unsupported';
  theorem_name?: string | null;
  lean_code?: string | null;
  audit?: LeanAuditReport | null;
  error_code?: string | null;
  error_message?: string | null;
};

type Verdict = {
  outcome: string;
  scope?: string;
  error_code?: string | null;
  reason_code?: string | null;
  missing_symbols?: string[];
  duration_ms?: number;
  witness?: unknown;
  counterexample?: unknown;
  domain_status?: string;
  domain_obligations?: DomainObligationItem[];
  applied_assumptions?: string[];
  failed_step_index?: number | null;
  steps?: ChainStepVerdict[];
  lean_export?: LeanExport;
};

export default function QuickVerifyPanel({ equation, onReview }: { equation: string; onReview: () => void }) {
  const [mode, setMode] = useState<'single' | 'chain'>('single');
  const [formula, setFormula] = useState('');
  const [chainSteps, setChainSteps] = useState<string[]>(['', '']);
  const [includeLean, setIncludeLean] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copiedLean, setCopiedLean] = useState(false);
  const [result, setResult] = useState<Verdict | null>(null);
  const retryKey = useRef('');

  const trimmedSteps = chainSteps.map(s => s.trim()).filter(Boolean);
  const canRun = mode === 'single' ? Boolean(formula.trim()) : trimmedSteps.length > 0;

  async function verify() {
    setBusy(true);
    setCopiedLean(false);
    if (!retryKey.current) retryKey.current = crypto.randomUUID();
    try {
      const requestBody: Record<string, unknown> = mode === 'single'
        ? { target_uuid: equation, formula_b: formula }
        : { target_uuid: equation, steps: trimmedSteps };
      if (includeLean) {
        requestBody.include_lean_export = true;
      }
      const response = await fetch('/api/checks/symbolic', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'idempotency-key': retryKey.current },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) throw new Error();
      const data = await response.json() as Verdict;
      if (data.scope !== 'ad_hoc_check_not_frozen_campaign') throw new Error();
      setResult(data);
      retryKey.current = '';
    } catch {
      setResult({ outcome: 'unavailable' });
    } finally {
      setBusy(false);
    }
  }

  function updateStep(index: number, value: string) {
    setChainSteps(prev => prev.map((item, idx) => (idx === index ? value : item)));
    setResult(null);
    retryKey.current = '';
  }

  function addStep() {
    if (chainSteps.length >= 8) return;
    setChainSteps(prev => [...prev, '']);
    setResult(null);
    retryKey.current = '';
  }

  function removeStep(index: number) {
    if (chainSteps.length <= 1) return;
    setChainSteps(prev => prev.filter((_, idx) => idx !== index));
    setResult(null);
    retryKey.current = '';
  }

  async function copyLeanCode(code: string) {
    try {
      await navigator.clipboard.writeText(code);
      setCopiedLean(true);
    } catch {
      setCopiedLean(false);
    }
  }

  const reason = result?.reason_code ?? result?.error_code;
  const preview = mode === 'single' ? renderFormulaHtml(formula) : '';

  return (
    <section className="inspector-section inspector-contracts">
      <h3>Quick Verify</h3>
      <small>ad-hoc check · not frozen campaign</small>

      <div className="quick-verify-mode-bar">
        <button
          type="button"
          aria-pressed={mode === 'single'}
          className={mode === 'single' ? 'is-active' : undefined}
          disabled={busy}
          onClick={() => { setMode('single'); setResult(null); retryKey.current = ''; }}
        >
          Single rewrite
        </button>
        <button
          type="button"
          aria-pressed={mode === 'chain'}
          className={mode === 'chain' ? 'is-active' : undefined}
          disabled={busy}
          onClick={() => { setMode('chain'); setResult(null); retryKey.current = ''; }}
        >
          Derivation chain
        </button>
      </div>

      {mode === 'single' ? (
        <>
          <label>
            Proposed rewrite
            <textarea
              value={formula}
              maxLength={2048}
              disabled={busy}
              onChange={e => {
                setFormula(e.target.value);
                setResult(null);
                retryKey.current = '';
              }}
            />
          </label>
          {formula && preview && (
            <div className="inspector-latex-math" dangerouslySetInnerHTML={{ __html: preview }} />
          )}
        </>
      ) : (
        <div className="quick-verify-chain-list">
          {chainSteps.map((step, index) => {
            const stepPreview = renderFormulaHtml(step);
            return (
              <div key={index} className="quick-verify-chain-step">
                <div className="quick-verify-chain-step-head">
                  <label htmlFor={`qv-step-${index}`}>Step {index + 1}</label>
                  {chainSteps.length > 1 && (
                    <button type="button" disabled={busy} onClick={() => removeStep(index)}>
                      Remove
                    </button>
                  )}
                </div>
                <input
                  id={`qv-step-${index}`}
                  value={step}
                  maxLength={2048}
                  disabled={busy}
                  placeholder={`Intermediate or target expression E_${index + 1}`}
                  onChange={e => updateStep(index, e.target.value)}
                />
                {step && stepPreview && (
                  <div className="inspector-latex-math" dangerouslySetInnerHTML={{ __html: stepPreview }} />
                )}
              </div>
            );
          })}
          {chainSteps.length < 8 && (
            <button type="button" className="quick-verify-add-step" disabled={busy} onClick={addStep}>
              + Add derivation step
            </button>
          )}
        </div>
      )}

      <label className="quick-verify-lean-toggle">
        <input
          type="checkbox"
          checked={includeLean}
          disabled={busy}
          onChange={e => { setIncludeLean(e.target.checked); setResult(null); retryKey.current = ''; }}
        />
        <span>Export audited Lean 4 statement</span>
      </label>

      <button type="button" disabled={busy || !canRun} onClick={() => void verify()}>
        {busy ? 'Checking…' : mode === 'chain' ? 'Verify Chain in Sandbox' : 'Verify in Sandbox'}
      </button>

      {result && (
        <div aria-live="polite">
          <div>
            <strong>{result.outcome}</strong>
            {result.duration_ms !== undefined && <span> · {result.duration_ms} ms</span>}
            {result.failed_step_index != null && (
              <span> · failed at step {result.failed_step_index}</span>
            )}
          </div>
          {reason && <p>{reason} {result.missing_symbols?.join(', ')}</p>}

          {result.applied_assumptions && result.applied_assumptions.length > 0 && (
            <div className="quick-verify-pills">
              {result.applied_assumptions.map(item => (
                <span key={item} className="quick-verify-pill is-assumption">{item}</span>
              ))}
            </div>
          )}

          {result.domain_obligations && result.domain_obligations.length > 0 && (
            <div className="quick-verify-obligations">
              {result.domain_obligations.map(ob => (
                <div key={ob.obligation_id} className="quick-verify-obligation-row">
                  <code>{ob.predicate}({ob.expression})</code>
                  <span className={`quick-verify-pill is-${ob.status}`}>{ob.status}</span>
                </div>
              ))}
            </div>
          )}

          {result.steps && result.steps.length > 0 && (
            <ol className="quick-verify-trace">
              {result.steps.map(step => (
                <li key={step.step_index} className={`is-${step.outcome}`}>
                  <div>
                    <strong>Step {step.step_index} · {step.outcome}</strong>
                    <span> · {step.duration_ms} ms</span>
                  </div>
                  <code>{step.from_formula} ⇒ {step.to_formula}</code>
                  {step.error_code && <small>{step.error_code}</small>}
                  {step.counterexample != null && (
                    <pre>{JSON.stringify(step.counterexample, null, 2)}</pre>
                  )}
                </li>
              ))}
            </ol>
          )}

          {result.witness != null && <pre>{JSON.stringify(result.witness, null, 2)}</pre>}
          {result.counterexample != null && <pre>{JSON.stringify(result.counterexample, null, 2)}</pre>}

          {result.lean_export && (
            <div className="quick-verify-lean-card">
              <div className="quick-verify-lean-head">
                <strong>Lean 4 Statement Audit</strong>
                {result.lean_export.audit && (
                  <span className={`quick-verify-pill is-proof-${result.lean_export.audit.proof_status.toLowerCase()}`}>
                    {result.lean_export.audit.proof_status}
                  </span>
                )}
              </div>
              {result.lean_export.status === 'unsupported' ? (
                <p>{result.lean_export.error_code}: {result.lean_export.error_message}</p>
              ) : (
                <>
                  {result.lean_export.audit && (
                    <div className="quick-verify-pills">
                      <span className={`quick-verify-pill is-${result.lean_export.audit.declaration_fidelity.toLowerCase()}`}>
                        DECLARATION_FIDELITY: {result.lean_export.audit.declaration_fidelity}
                      </span>
                      <span className={`quick-verify-pill is-${result.lean_export.audit.draft_allowlist.toLowerCase()}`}>
                        DRAFT_ALLOWLIST: {result.lean_export.audit.draft_allowlist}
                      </span>
                    </div>
                  )}
                  {result.lean_export.audit && result.lean_export.audit.binder_table.length > 0 && (
                    <table className="quick-verify-binder-table">
                      <thead>
                        <tr><th>Binder</th><th>Type</th><th>Bin</th></tr>
                      </thead>
                      <tbody>
                        {result.lean_export.audit.binder_table.map((row, idx) => (
                          <tr key={`${row.name}-${idx}`}>
                            <td><code>{row.name}</code></td>
                            <td><code>{row.type_expr}</code></td>
                            <td><code>{row.bin}</code></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {result.lean_export.lean_code && (
                    <>
                      <pre>{result.lean_export.lean_code}</pre>
                      <button
                        type="button"
                        onClick={() => void copyLeanCode(result.lean_export?.lean_code ?? '')}
                      >
                        {copiedLean ? 'Copied Lean 4' : 'Copy Lean 4 draft'}
                      </button>
                    </>
                  )}
                </>
              )}
            </div>
          )}

          {(reason?.startsWith('UNRESOLVED_') || reason === 'CONTRADICTORY_DOMAIN') && (
            <button type="button" onClick={onReview}>Open Batch Review</button>
          )}
        </div>
      )}
    </section>
  );
}
