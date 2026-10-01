import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

it('patched legacy Drizzle loader compiles schema/config and validates existing migration history', () => {
  const require = createRequire(import.meta.url);
  const core = require('@esbuild-kit/core-utils');
  const nested = createRequire(require.resolve('@esbuild-kit/core-utils'));
  const version = nested('esbuild/package.json').version.split('.').map(Number);
  expect(version[0] > 0 || version[1] >= 25).toBe(true);
  for (const filename of ['db/schema.ts', 'drizzle.config.ts']) {
    const absolute = fileURLToPath(new URL(`../${filename}`, import.meta.url));
    expect(core.transformSync(readFileSync(absolute, 'utf8'), absolute).code.length).toBeGreaterThan(0);
  }
  const output = execFileSync(process.execPath, [
    'node_modules/drizzle-kit/bin.cjs', 'check', '--config=drizzle.config.ts',
  ], { cwd: fileURLToPath(new URL('..', import.meta.url)), encoding: 'utf8', timeout: 20_000 });
  expect(output).toContain('Everything');
});
