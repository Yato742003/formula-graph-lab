// @vitest-environment jsdom
import {fireEvent, render, screen} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import QuickVerifyPanel from '../components/quick-verify-panel';

it('sends only the proposed rewrite and opens review on a blocked verdict', async () => {
  const review = vi.fn();
  vi.stubGlobal('fetch', async (_: string, init: RequestInit) => {
    expect(JSON.parse(init.body as string)).toEqual({target_uuid:'eq', formula_b:'x+x'});
    return Response.json({outcome:'unknown', reason_code:'UNRESOLVED_SHAPE', missing_symbols:['x'],
      scope:'ad_hoc_check_not_frozen_campaign'});
  });
  try {
    render(<QuickVerifyPanel equation="eq" onReview={review}/>);
    fireEvent.change(screen.getByLabelText('Proposed rewrite'), {target:{value:'x+x'}});
    fireEvent.click(screen.getByText('Verify in Sandbox'));
    fireEvent.click(await screen.findByText('Open Batch Review'));
    expect(review).toHaveBeenCalledOnce();
    expect(screen.getByText('ad-hoc check · not frozen campaign')).toBeTruthy();
  } finally {vi.unstubAllGlobals();}
});
