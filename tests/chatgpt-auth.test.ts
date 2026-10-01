import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ headers: vi.fn(), env: {
  APP_ENV: 'development', FGL_TRUST_SITES_IDENTITY: 'false',
} }));
vi.mock('next/headers', () => ({ headers: mocks.headers }));
vi.mock('next/navigation', () => ({ redirect: vi.fn() }));
vi.mock('cloudflare:workers', () => ({ env: mocks.env }));
import { chatGPTSignInPath, chatGPTSignOutPath, getChatGPTUser } from '../app/chatgpt-auth';

afterEach(() => {
  mocks.env.APP_ENV = 'development';
  mocks.env.FGL_TRUST_SITES_IDENTITY = 'false';
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

function identity(extra: Record<string, string> = {}) {
  return new Headers({ 'oai-authenticated-user-id': 'user-1',
    'oai-authenticated-user-email': 'user@example.test',
    'oai-authenticated-user-full-name': encodeURIComponent('Người dùng'),
    'oai-authenticated-user-full-name-encoding': 'percent-encoded-utf-8', ...extra });
}

it('requires explicitly trusted Sites ingress outside local development', async () => {
  mocks.headers.mockResolvedValue(identity());
  mocks.env.APP_ENV = 'production';
  expect(await getChatGPTUser()).toBeNull();
  expect(mocks.headers).not.toHaveBeenCalled();
  mocks.env.FGL_TRUST_SITES_IDENTITY = 'true';
  expect(await getChatGPTUser()).toMatchObject({ userId: 'user-1', displayName: 'Người dùng' });
});

it('a production build cannot enable local identity by setting APP_ENV=development', async () => {
  vi.stubEnv('DEV', false);
  mocks.headers.mockResolvedValue(identity());
  expect(await getChatGPTUser()).toBeNull();
  expect(mocks.headers).not.toHaveBeenCalled();
});

it('denies missing or malformed identity and never accepts a browser role', async () => {
  for (const headers of [new Headers(), identity({ 'oai-authenticated-user-id': '' }),
    identity({ 'oai-authenticated-user-id': 'u'.repeat(201) }),
    identity({ 'oai-authenticated-user-id': 'user with spaces' }),
    identity({ 'oai-authenticated-user-email': 'e'.repeat(321) })]) {
    mocks.headers.mockResolvedValue(headers);
    expect(await getChatGPTUser()).toBeNull();
  }
  mocks.headers.mockResolvedValue(identity({ 'x-fgl-actor-role': 'admin' }));
  expect(await getChatGPTUser()).not.toHaveProperty('role');
});

it('falls back to email for malformed or oversized display names', async () => {
  for (const name of ['%badencoding', 'x'.repeat(2001)]) {
    mocks.headers.mockResolvedValue(identity({ 'oai-authenticated-user-full-name': name }));
    expect((await getChatGPTUser())?.displayName).toBe('user@example.test');
  }
});

it.each(['https://evil.test', '//evil.test', '/\\evil.test', '/signin-with-chatgpt',
  '/signout-with-chatgpt', '/callback'])('keeps auth redirects internal: %s', path => {
  expect(chatGPTSignInPath(path)).toBe('/signin-with-chatgpt?return_to=%2F');
  expect(chatGPTSignOutPath(path)).toBe('/signout-with-chatgpt?return_to=%2F');
});
