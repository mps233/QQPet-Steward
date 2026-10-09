import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from '../../node_modules/typescript/lib/typescript.js'

function compile(url) {
  return ts.transpileModule(fs.readFileSync(url, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
}

let response
const api = {}
new Function('exports', 'fetch', compile(new URL('../api.ts', import.meta.url)))(
  api, async () => response,
)
response = { ok: false, status: 400, json: async () => ({
  ok: false, msg: 'Telegram: missing credentials', results: { Telegram: 'missing credentials' },
}) }
assert.equal((await api.testNotify()).msg, 'Telegram: missing credentials')
response = { ok: true, status: 200, json: async () => ({
  ok: true, msg: 'Telegram: success; desktop: failed',
}) }
assert.equal((await api.testNotify()).msg, 'Telegram: success; desktop: failed')
response = { ok: false, status: 500, json: async () => { throw new Error('not JSON') } }
await assert.rejects(api.testNotify(), /HTTP 500/)

// Execute the actual component with minimal hooks, without a browser or network.
let hookIndex = 0
let hooks = []
const useState = (initial) => {
  const index = hookIndex++
  if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial
  return [hooks[index], (value) => {
    hooks[index] = typeof value === 'function' ? value(hooks[index]) : value
  }]
}
const useRef = (value) => useState(() => ({ current: value }))[0]
const jsx = (type, props) => ({ type, props })
const form = Object.fromEntries(['FGroup', 'FRow', 'NoteRow', 'Switch'].map((key) =>
  [key, function Component() {}]))
let sent = 0
const modules = {
  react: { useState, useRef, useEffect: () => {}, useCallback: (fn) => fn },
  'react/jsx-runtime': { jsx, jsxs: jsx },
  './form': form,
  '../api': {
    fetchNotifyHistory: async () => ({ records: [] }),
    testNotify: async () => {
      sent++
      return { ok: false, msg: 'Telegram: detailed failure' }
    },
  },
}
const component = {}
new Function('exports', 'require', compile(new URL('../components/NotifyPage.tsx', import.meta.url)))(
  component, (name) => {
    assert.ok(name in modules, `Unexpected import: ${name}`)
    return modules[name]
  },
)
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return []
  if (Array.isArray(tree)) return tree.flatMap(nodes)
  return [tree, ...nodes(tree.props?.children)]
}
function render(onSave) {
  hookIndex = 0
  return nodes(component.NotifyPage({
    editable: { notify_feishu_enabled: false },
    onSave,
  }))
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
let view = render(async () => { throw new Error('save rejected') })
view.find((node) => node.type === form.Switch).props.onToggle()
assert.equal(render(async () => {}).find((node) => node.type === form.Switch).props.on, true)
await tick()
view = render(async () => {})
assert.equal(view.find((node) => node.type === form.Switch).props.on, false)
assert.match(view.find((node) => node.props?.role === 'alert').props.children, /save rejected/)

hooks = []
let finishSave
view = render(() => new Promise((resolve) => { finishSave = resolve }))
view.find((node) => node.type === form.Switch).props.onToggle()
view.find((node) => node.type === 'button' && node.props.children === '发送测试通知').props.onClick()
await tick()
assert.equal(sent, 0, 'Test must wait for an in-flight save')
finishSave()
await tick()
assert.equal(sent, 1)
assert.match(render(async () => {}).find((node) => node.props?.id === 'notifyTestMsg').props.children,
  /detailed failure/)

console.log('Notification API details, rollback and save-before-test checks passed')
