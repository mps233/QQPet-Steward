import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

let nextModule = 0

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

const settle = () => new Promise((resolve) => setImmediate(resolve))

function fakeElement() {
  const listeners = new Map()
  return {
    dataset: {},
    disabled: false,
    textContent: '',
    addEventListener(name, listener) {
      listeners.set(name, listener)
    },
    async dispatch(name, event = {}) {
      await listeners.get(name)?.(event)
    },
  }
}

async function loadLauncher({ invoke, initialError, native = true } = {}) {
  const elements = new Map()
  const listeners = new Map()
  const calls = []
  const testWindow = {
    addEventListener(name, listener) {
      listeners.set(name, listener)
    },
    dispatchEvent(event) {
      listeners.get(event.type)?.(event)
    },
  }
  if (native) {
    testWindow.__TAURI_INTERNALS__ = {
      invoke: async (command, payload) => {
        calls.push([command, payload])
        if (invoke) return invoke(command, payload)
        return command === 'get_startup_status' ? { phase: 'starting', message: '' } : null
      },
    }
  }
  if (initialError) testWindow.__qpetConnectionError = initialError
  globalThis.window = testWindow
  globalThis.document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, fakeElement())
      return elements.get(selector)
    },
  }
  await import(`../app/launcher.js?test=${++nextModule}`)
  await settle()
  return {
    calls,
    elements,
    window: testWindow,
    reportError(message) {
      testWindow.__qpetConnectionError = message
      testWindow.dispatchEvent({ type: 'qpet-connection-error', detail: message })
    },
  }
}

{
  const html = await readFile(new URL('../app/index.html', import.meta.url), 'utf8')
  assert.doesNotMatch(html, /<input\b|<form\b|访问码|配对|远程|服务器|127\.0\.0\.1|https?:\/\//)
  assert.match(html, /正在启动 QQPet-Steward/)
  assert.match(html, /id="retry"[^>]*disabled>重试启动/)
}

{
  const launcher = await loadLauncher()
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'starting')
  assert.equal(launcher.elements.get('#status-title').textContent, '正在启动 QQPet-Steward')
  assert.equal(launcher.elements.get('#retry').disabled, true)
  assert.deepEqual(launcher.calls.map(([command]) => command), ['get_startup_status'])
}

{
  const launcher = await loadLauncher({
    invoke: async () => ({ phase: 'ready', message: '' }),
  })
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'ready')
  assert.equal(launcher.elements.get('#status-title').textContent, '正在打开主界面')
  assert.equal(launcher.elements.get('#retry').disabled, true)
}

{
  const launcher = await loadLauncher({
    invoke: async (command) => command === 'get_startup_status'
      ? { phase: 'error', message: '后台进程未能启动' } : null,
  })
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  assert.equal(launcher.elements.get('#status-detail').textContent, '后台进程未能启动')
  assert.equal(launcher.elements.get('#retry').disabled, false)

  await launcher.elements.get('#retry').dispatch('click')
  assert.equal(launcher.window.__qpetConnectionError, undefined)
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'ready')
  assert.equal(launcher.elements.get('#retry').disabled, true)
  assert.deepEqual(launcher.calls.map(([command]) => command), ['get_startup_status', 'retry_startup'])
}

{
  const launcher = await loadLauncher({
    invoke: async (command) => {
      if (command === 'get_startup_status') return { phase: 'error', message: '首次启动失败' }
      throw new Error('后台进程提前退出')
    },
  })
  await launcher.elements.get('#retry').dispatch('click')
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  assert.match(launcher.elements.get('#status-detail').textContent, /后台进程提前退出/)
  assert.equal(launcher.elements.get('#retry').disabled, false)
}

{
  const pending = deferred()
  const launcher = await loadLauncher({ invoke: async () => pending.promise })
  launcher.reportError('无法打开主界面')
  pending.resolve({ phase: 'ready', message: '后台已就绪' })
  await settle()
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  assert.equal(launcher.elements.get('#status-detail').textContent, '无法打开主界面')
  assert.equal(launcher.elements.get('#retry').disabled, false)
}

{
  const launcher = await loadLauncher({
    initialError: '启动过程中发生错误',
    invoke: async (command) => command === 'get_startup_status'
      ? { phase: 'ready', message: '' } : null,
  })
  assert.equal(launcher.elements.get('#status-detail').textContent, '启动过程中发生错误')
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  await launcher.elements.get('#retry').dispatch('click')
  assert.equal(launcher.window.__qpetConnectionError, undefined)
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'ready')
}

{
  const pending = deferred()
  const launcher = await loadLauncher({
    invoke: async (command) => command === 'get_startup_status'
      ? { phase: 'error', message: '首次启动失败' } : pending.promise,
  })
  const retry = launcher.elements.get('#retry').dispatch('click')
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'starting')
  assert.equal(launcher.elements.get('#retry').disabled, true)
  launcher.reportError('重新启动时发生错误')
  pending.resolve(null)
  await retry
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  assert.equal(launcher.elements.get('#status-detail').textContent, '重新启动时发生错误')
  assert.equal(launcher.elements.get('#retry').disabled, false)
}

{
  const launcher = await loadLauncher({ native: false })
  assert.equal(launcher.elements.get('.status-card').dataset.state, 'error')
  assert.match(launcher.elements.get('#status-detail').textContent, /请通过 macOS 应用打开/)
  assert.equal(launcher.elements.get('#retry').disabled, false)
}

console.log('launcher tests: ok')
