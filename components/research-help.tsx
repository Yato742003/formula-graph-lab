'use client';

import { BookOpen, Check, ChevronDown, ListChecks } from 'lucide-react';
import { useState } from 'react';
import { useWorkspaceLocation, withContext } from './workspace-shell';

const onboardingSteps = [
  { id: 'import', label: 'Import an HTML paper', detail: 'Create the first source-backed evidence set.', href: '/graph#paper-import' },
  { id: 'spec', label: 'Freeze a ProblemSpec', detail: 'Turn the research question into a versioned rulebook.', href: '/spec' },
  { id: 'mapping', label: 'Review compatibility', detail: 'Check whether two typed ports can connect.', href: '/compatibility' },
  { id: 'candidate', label: 'Create a candidate', detail: 'Use an allowlisted transformation with immutable parents.', href: '/proposals' },
  { id: 'report', label: 'Read and replay the report', detail: 'Inspect scoped evidence before making a claim.', href: '/reports' },
] as const;

const checklistStorageKey = 'fgl-help-checklist-v1';

function readChecklist(): Set<string> {
  // ponytail: keep checklist progress local, ceiling: no cross-device resume, upgrade: persist per-user progress when beta needs shared onboarding state.
  try {
    const saved = typeof window === 'undefined' ? null : window.localStorage?.getItem(checklistStorageKey);
    const parsed: unknown = saved ? JSON.parse(saved) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : []);
  } catch {
    return new Set();
  }
}

export function OnboardingChecklist() {
  const { params } = useWorkspaceLocation();
  const [completed, setCompleted] = useState<Set<string>>(readChecklist);
  const [expanded, setExpanded] = useState(false);

  function toggle(id: string) {
    setCompleted((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try { window.localStorage?.setItem(checklistStorageKey, JSON.stringify([...next])); } catch { /* ignore unavailable storage */ }
      return next;
    });
  }

  const count = onboardingSteps.filter((step) => completed.has(step.id)).length;

  return (
    <section className="onboarding-checklist" aria-labelledby="onboarding-checklist-title">
      <header className="onboarding-checklist-header">
        <div>
          <p className="eyebrow"><ListChecks size={13} aria-hidden="true" /> Research path</p>
          <h2 id="onboarding-checklist-title">One paper to one report</h2>
          <p>{count} of {onboardingSteps.length} steps marked complete. You can return to any step later.</p>
        </div>
        <div className="onboarding-checklist-actions">
          <progress max={onboardingSteps.length} value={count} aria-label={`${count} of ${onboardingSteps.length} research steps complete`} />
          <button
            type="button"
            className="onboarding-toggle"
            aria-expanded={expanded}
            aria-controls="onboarding-checklist-list"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? 'Hide steps' : 'Show steps'}
            <ChevronDown size={13} aria-hidden="true" />
          </button>
        </div>
      </header>
      {expanded ? <ol className="onboarding-checklist-list" id="onboarding-checklist-list">
        {onboardingSteps.map((step, index) => {
          const isDone = completed.has(step.id);
          return (
            <li key={step.id} className={isDone ? 'is-complete' : undefined}>
              <label>
                <input type="checkbox" checked={isDone} onChange={() => toggle(step.id)} />
                <span className="onboarding-step-number">{isDone ? <Check size={13} aria-hidden="true" /> : index + 1}</span>
                <span className="onboarding-step-copy">
                  <strong>{step.label}</strong>
                  <small>{step.detail}</small>
                </span>
              </label>
              <a href={withContext(step.href, params)} aria-label={`Open ${step.label}`}>Open</a>
            </li>
          );
        })}
      </ol> : null}
    </section>
  );
}

const recipes = [
  {
    id: 'graph', number: '01', title: 'Import and inspect a paper',
    summary: 'Keep every formula linked to its paper and source anchor.', href: '/graph#paper-import', label: 'Open Graph',
    steps: ['Paste an arXiv HTML URL and select Import paper. Wait for the saved paper receipt.', 'Select an equation in the graph or mobile node list. Use Open evidence in source to inspect the original.', 'Use Multi-paper lineage to select another indexed paper. Filters narrow the graph; Fit all evidence restores the overview.'],
    details: '2D is the default. 3D, compact nodes, Zen and fullscreen change the view only. Search opens with Ctrl+K or /. Add evidence creates a source-backed lineage assertion, port mapping or symbol contract review; a citation is not a mathematical derivation.',
  },
  {
    id: 'spec', number: '02', title: 'Freeze a research question',
    summary: 'Fix the rules before comparing candidates.', href: '/spec', label: 'Open Spec',
    steps: ['Write a research question, method family and dtype.', 'Open the advanced manifest. Pin the dataset, splits, baseline, evaluator, seeds, budget and allowed transforms; format and validate the JSON.', 'Review the summary and freeze. Use snapshot history to reopen a version or create a revision.'],
    details: 'A frozen ProblemSpec is immutable. Freezing validates the manifest; it does not verify artifact bytes or run an experiment. A revision creates a new version. The 3DGS template is a starting point: replace its placeholder inputs before freezing.',
  },
  {
    id: 'compatibility', number: '03', title: 'Review a connection',
    summary: 'Decide whether one formula output can be used as another input.', href: '/compatibility', label: 'Open Compatibility',
    steps: ['Open Add evidence and choose Typed port mapping.', 'Select producer and consumer equations, their scoped symbols and source anchors.', 'Open a mapping, inspect unresolved gates and record a human review with its rationale.'],
    details: 'The gates cover shape, domain, normalization, masks, causality and symbol bindings. Similar-looking formulas may still be incompatible. Unknown or stale mappings cannot become eligible parents simply because a model suggests them. Review symbol contracts through Add evidence when a binding needs confirmation.',
  },
  {
    id: 'proposals', number: '04', title: 'Create a bounded hypothesis',
    summary: 'Compile a declared transformation or review an AI draft.', href: '/proposals#proposal-builder', label: 'Open Proposals',
    steps: ['Choose a frozen spec that allows the operator and a current, reviewed compatible mapping.', 'Set the mixture weight between 0 and 1, inspect the preview, then compile.', 'For an AI draft, open Propose, inspect the model, cost caps and selected sources before consenting. Review the saved proposal explicitly before compilation.'],
    details: 'AI may propose a transform. It cannot approve, verify, change the spec or start an experiment. Compilation records immutable parents and obligations. A candidate and a positive scoped check do not establish overall research performance.',
  },
  {
    id: 'reports', number: '05', title: 'Read and replay evidence',
    summary: 'Inspect the scope of a result before making a claim.', href: '/reports#report-view', label: 'Open Reports',
    steps: ['Select a saved candidate. Add at most two candidates to compare.', 'Inspect checker outcome, assumptions, parents and versions. Open the numerical fixture and research case only when needed.', 'Open the replay report to export its bundle. Select a frozen spec from Spec to inspect its evolution campaign.'],
    details: 'Replay reconstructs recorded inputs and receipts; it does not invent missing experiments. Synthetic CPU fixtures are engineering checks, not product-performance claims. Admission is a policy decision, not a started run.',
  },
] as const;

export function HelpCenter() {
  const { params } = useWorkspaceLocation();
  return (
    <div className="help-center">
      <section className="help-hero" aria-labelledby="help-title">
        <p className="eyebrow"><BookOpen size={13} aria-hidden="true" /> User guide</p>
        <h1 id="help-title">From paper to evidence</h1>
        <p>Import a paper, connect its formulas and test a research hypothesis. Start with one task below and return here whenever you need the next step.</p>
      </section>

      <nav className="help-topics" aria-label="Guide topics">
        <a href="#workflow">Workflow</a>
        {recipes.map((recipe) => <a key={recipe.id} href={`#${recipe.id}`}>{recipe.label.replace('Open ', '')}</a>)}
        <a href="#glossary">Glossary</a>
        <a href="#troubleshooting">Troubleshooting</a>
      </nav>

      <OnboardingChecklist />

      <section id="workflow" className="help-section" aria-labelledby="help-recipes-title">
        <header className="help-section-heading">
          <div><p className="eyebrow">Recipes</p><h2 id="help-recipes-title">Choose the task you want to finish</h2></div>
          <span>5 short paths</span>
        </header>
        <div className="help-recipe-grid">
          {recipes.map((recipe) => (
            <article id={recipe.id} className="help-recipe-card" key={recipe.id}>
              <span className="help-recipe-number">{recipe.number}</span>
              <h3>{recipe.title}</h3>
              <p>{recipe.summary}</p>
              <ol>
                {recipe.steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
              <details><summary>Details and limits</summary><p>{recipe.details}</p></details>
              <a href={withContext(recipe.href, params)}>{recipe.label}</a>
            </article>
          ))}
        </div>
      </section>

      <section id="glossary" className="help-section" aria-labelledby="help-glossary-title">
        <header className="help-section-heading"><div><p className="eyebrow">Glossary</p><h2 id="help-glossary-title">Words you will see in the lab</h2></div></header>
        <dl className="help-glossary">
          <div><dt>ProblemSpec</dt><dd>The research question and fixed rules for a run.</dd></div>
          <div><dt>Lineage</dt><dd>The source-backed family tree of papers, formulas and transformations.</dd></div>
          <div><dt>Compatibility</dt><dd>Evidence that one typed port can connect to another.</dd></div>
          <div><dt>Candidate</dt><dd>A compiled hypothesis with immutable parents and recorded obligations.</dd></div>
          <div><dt>Unknown</dt><dd>There is not enough evidence yet. It is not a pass.</dd></div>
          <div><dt>Conditional</dt><dd>The result depends on stated assumptions. Read them before using it.</dd></div>
          <div><dt>Unsupported / timeout / error</dt><dd>The checker could not complete a supported decision. No success is inferred.</dd></div>
          <div><dt>Not run</dt><dd>No execution evidence exists for this stage yet.</dd></div>
          <div><dt>Replay</dt><dd>Rebuilding a recorded result from its frozen inputs and versions.</dd></div>
        </dl>
      </section>

      <section id="troubleshooting" className="help-section" aria-labelledby="help-troubleshooting-title">
        <header className="help-section-heading"><h2 id="help-troubleshooting-title">When you get stuck</h2></header>
        <details className="research-disclosure"><summary>No eligible spec or mapping</summary><div className="research-disclosure-body">Freeze a spec that allows the displayed operator. Review a compatible mapping with scoped producer and consumer symbols. Unknown, unreviewed or stale mappings need evidence before compilation.</div></details>
        <details className="research-disclosure"><summary>AI drafting is unavailable</summary><div className="research-disclosure-body">The provider, model and request/daily cost caps must be configured. Keep using the deterministic builder while configuration is unavailable. A failed request does not create a proposal.</div></details>
        <details className="research-disclosure"><summary>The graph is empty or too small</summary><div className="research-disclosure-body">Check the import receipt, reset filters and select Fit all evidence. On mobile, select a node from the list; the canvas is available in advanced view.</div></details>
        <details className="research-disclosure"><summary>Records could not be loaded</summary><div className="research-disclosure-body">Reload the page and check that the local graph service is running. Existing evidence is retained. Do not interpret missing data as a successful check.</div></details>
        <details className="research-disclosure"><summary>Subsystems, quotas and worker job recovery</summary><div className="research-disclosure-body">Visit the <a href={withContext('/ops', params)} style={{ color: 'var(--primary)', textDecoration: 'underline' }}>Operations &amp; Health Dashboard</a> to inspect CAS/SymPy worker RAM limits, execution deadlines, quota consumption, and sweep/recover stuck jobs.</div></details>
        <details className="research-disclosure"><summary>Keyboard shortcuts</summary><div className="research-disclosure-body">Use Ctrl+K or / for search and Escape to close it. In the graph, arrow keys move selection; Enter or Space selects a node. Z toggles Zen. Tab moves between controls; Enter opens a disclosure.</div></details>
      </section>
    </div>
  );
}
