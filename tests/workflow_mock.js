// Runs skills/agent-journal/scripts/catch-up.workflow.js against mocked agents and checks
// that no session falls between the cracks when agents misbehave. Usage: node tests/workflow_mock.js
const assert = require('assert')
const fs = require('fs')
const path = require('path')

const script = path.join(__dirname, '..', 'skills', 'agent-journal', 'scripts', 'catch-up.workflow.js')
const src = fs.readFileSync(script, 'utf8').replace(/^export const meta/m, 'const meta')
const AsyncFunction = (async () => {}).constructor
const run = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', src)

// The Workflow tool's hooks, as documented: a throwing thunk or stage resolves to null.
const parallel = thunks => Promise.all(thunks.map(t => t().catch(() => null)))
const pipeline = (items, ...stages) => Promise.all(items.map(async item => {
  let r = item
  try {
    for (const stage of stages) r = await stage(r)
  } catch {
    return null
  }
  return r
}))

const dec = (index, decision, extra = {}) =>
  ({ index, decision, project: 'proj', work: `work ${index}`, shipped: false, reason: 'r', ...extra })
const entry = (markdown, extra = {}) => ({ markdown, win: false, extends: '', ...extra })

// Each day is one scenario; the mock answers by the agent's label.
const scenarios = {
  '2026-01-01': { // lessons: one duplicated, one missing, one stray index
    keys: ['a0', 'a1', 'a2'],
    triage: { decisions: [dec(0, 'lesson'), dec(1, 'lesson'), dec(2, 'skip', { shipped: true })] },
    group: { groups: [], lessons: [{ index: 0, project: 'Lessons' }] },
    lessons: { lessons: [{ index: 0, markdown: '### a' }, { index: 0, markdown: '### dup' }, { index: 7, markdown: 'x' }] },
  },
  '2026-01-02': { // triage repeats an index and invents one, on both attempts; grouping fails
    keys: ['b0', 'b1', 'b2'],
    triage: { decisions: [dec(0, 'entry'), dec(0, 'entry'), dec(9, 'skip')] },
    group: null,
  },
  '2026-01-03': { // drafting fails
    keys: ['c0'],
    triage: { decisions: [dec(0, 'entry')] },
    draft: null,
  },
  '2026-01-04': { // a long new entry is trimmed; a long extended one isn't
    keys: ['d0', 'd1'],
    triage: { decisions: [dec(0, 'entry'), dec(1, 'entry')] },
    group: { groups: [{ project: 'P', work: 'w0', indexes: [0] }, { project: 'P', work: 'w1', indexes: [1] }], lessons: [] },
    draft: label => entry('w '.repeat(200), label.endsWith('#1') ? { extends: '### old' } : { win: true }),
  },
}

const calls = []
async function agent(prompt, opts) {
  calls.push(opts.label)
  const date = Object.keys(scenarios).find(d => opts.label.includes(d))
  const s = scenarios[date]
  if (opts.label.startsWith('triage')) return s.triage
  if (opts.label.startsWith('group')) return s.group
  if (opts.label.startsWith('lessons')) return s.lessons
  if (opts.label.startsWith('draft')) {
    if (typeof s.draft === 'function') return s.draft(prompt.includes('"w1"') ? 'draft#1' : 'draft#0')
    return s.draft === undefined ? entry('### e\n- **Did:** x') : s.draft
  }
  if (opts.label.startsWith('trim')) return entry('### short', { win: false })
  throw new Error(`unexpected agent ${opts.label}`)
}

const days = Object.entries(scenarios).map(([date, s]) =>
  ({ date, candidates_file: `/tmp/it's ${date}.json`, note: `/tmp/journal/${date}.md`, keys: s.keys }))

run(agent, parallel, pipeline, () => {}, () => {}, { skill_dir: '/skill', scope: 's', days }, {}, null).then(result => {
  const byDate = Object.fromEntries(result.days.map(d => [d.date, d]))

  // Every key ends up judged, unjudged, or in a recorded problem; none silently vanishes.
  for (const d of result.days) {
    const accounted = new Set([...d.decisions.map(x => x.key), ...d.unjudged])
    assert.deepStrictEqual([...accounted].sort(), [...scenarios[d.date].keys].sort(), `${d.date} coverage`)
  }

  const l = byDate['2026-01-01']
  assert.deepStrictEqual(l.lessons, [
    { key: 'a0', project: 'Lessons', markdown: '### a' },
    { key: 'a1', project: 'proj', markdown: '' },
  ])
  assert.ok(l.problems.some(p => p.includes('a1')), 'missing lesson is a problem')
  assert.deepStrictEqual(l.decisions.find(x => x.key === 'a2'), { key: 'a2', decision: 'skip', reason: 'r', shipped: true })

  const t = byDate['2026-01-02']
  assert.deepStrictEqual(t.unjudged, ['b1', 'b2'])
  assert.strictEqual(calls.filter(c => c.startsWith('triage 2026-01-02')).length, 2, 'triage retried once')
  assert.deepStrictEqual(t.entries.map(e => e.keys), [['b0']])
  assert.ok(t.problems.some(p => p.startsWith('group 2026-01-02 failed')))

  assert.deepStrictEqual(byDate['2026-01-03'].entries, [])
  assert.ok(byDate['2026-01-03'].problems.some(p => p.includes('c0')))

  const long = byDate['2026-01-04'].entries
  const fresh = long.find(e => e.keys[0] === 'd0')
  const extended = long.find(e => e.keys[0] === 'd1')
  assert.strictEqual(fresh.markdown, '### short', 'new long entry trimmed')
  assert.strictEqual(fresh.win, true, 'trim keeps the draft\'s win')
  assert.strictEqual(extended.extends, '### old')
  assert.strictEqual(extended.words, 200, 'extended entry not trimmed')
  assert.strictEqual(calls.filter(c => c.startsWith('trim')).length, 1)

  console.log(`ok: ${calls.length} mocked agents, ${result.days.length} scenarios`)
}).catch(err => {
  console.error(err.stack || err)
  process.exit(1)
})
