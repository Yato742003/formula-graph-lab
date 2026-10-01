// @vitest-environment jsdom

import './setup';

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { HelpCenter, OnboardingChecklist } from '../components/research-help';

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      clear: () => values.clear(),
    },
  });
});

describe('research help', () => {
  it('stores onboarding progress locally without changing the research state', async () => {
    const user = userEvent.setup();
    render(<OnboardingChecklist />);

    const toggle = screen.getByRole('button', { name: 'Show steps' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    await user.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const first = screen.getAllByRole('checkbox')[0];
    expect(screen.getByText(/0 of 5 steps marked complete/)).toBeTruthy();
    await user.click(first);

    expect(screen.getByText(/1 of 5 steps marked complete/)).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem('fgl-help-checklist-v1') ?? '[]')).toEqual(['import']);
  });

  it('publishes task recipes and a small glossary', () => {
    render(<HelpCenter />);

    expect(screen.getByRole('heading', { name: 'From paper to evidence' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Choose the task you want to finish' })).toBeTruthy();
    expect(screen.getByText('ProblemSpec')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Graph' }).getAttribute('href')).toBe('/graph#paper-import');
    expect(screen.getByRole('navigation', { name: 'Guide topics' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'When you get stuck' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show steps' })).toBeTruthy();
  });
});
