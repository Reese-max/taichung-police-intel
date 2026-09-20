import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import test from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');

function findPython() {
  const candidates = process.platform === 'win32'
    ? [{ command: 'python', prefix: [] }, { command: 'py', prefix: ['-3'] }]
    : [{ command: 'python3', prefix: [] }, { command: 'python', prefix: [] }];
  for (const candidate of candidates) {
    const result = spawnSync(candidate.command, [...candidate.prefix, '--version'], { stdio: 'ignore' });
    if (result.status === 0) return candidate;
  }
  throw new Error('No supported Python interpreter found');
}

const python = findPython();
function runPython(args) {
  return spawnSync(python.command, [...python.prefix, ...args], { cwd: repo, encoding: 'utf8' });
}

test('source policy promotion/retirement/query coverage suite passes', () => {
  const result = runPython(['-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_source_policy.py', '-v']);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran [1-9]\d* tests?/);
  assert.match(result.stderr, /OK/);
});

test('source policy CLI self-check compiles current source catalog', () => {
  const result = runPython(['-X', 'utf8', 'scripts/source-policy.py', '--self-check']);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /SOURCE_POLICY_SELF_CHECK_OK/);
});
