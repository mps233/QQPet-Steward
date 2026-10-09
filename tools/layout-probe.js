// Browser-only, read-only probe embedded by layout-check.sh.
(() => {
  const frame = document.getElementById('f')
  const page = frame.dataset.page
  const size = `${frame.width}x${frame.height}`
  let opened = false
  let mediaStarted = false
  let stableTicks = 0
  let ticks = 0
  let done = false

  const publish = (result) => {
    if (done) return
    done = true
    document.body.setAttribute('data-probe', encodeURIComponent(JSON.stringify(result)))
  }

  frame.addEventListener('load', () => {
    const tick = () => {
      if (done) return
      const d = frame.contentDocument
      const w = frame.contentWindow
      const slot = d?.querySelector('.page-slot.page-enter')
      const sec = slot?.querySelector('section[data-page]')
      const home = sec?.matches('.v2-home, .home') ? sec : null
      const video = sec?.querySelector('video')
      let ready = !!sec

      if (page.startsWith('main')) {
        ready = !!(home && sec.querySelector('.qpanel') && sec.querySelectorAll('.cap').length >= 3)
        if (ready && page === 'main-open' && !opened) {
          sec.querySelector('#idRing').click()
          opened = true
          ready = false
        }
      }
      if (page === 'shot' && video) {
        if (!mediaStarted) {
          const canvas = d.createElement('canvas')
          canvas.width = 720
          canvas.height = 1280
          const ctx = canvas.getContext('2d')
          ctx.fillRect(0, 0, canvas.width, canvas.height)
          video.srcObject = canvas.captureStream(5)
          void video.play().catch(() => {})
          // Retain the canvas until the browser has produced video metadata.
          frame.auditCanvas = canvas
          mediaStarted = true
        }
        ready = video.videoWidth > 0
      }
      stableTicks = ready ? stableTicks + 1 : 0
      if (++ticks > 60 && stableTicks < 2) {
        publish({ size, page, probeMissing: true })
        return
      }
      if (stableTicks < 2) {
        setTimeout(tick, 250)
        return
      }

      const q = (s) => sec.querySelector(s)
      const rect = (e) => {
        if (!e) return null
        const b = e.getBoundingClientRect()
        return [b.left, b.top, b.width, b.height].map((n) => +n.toFixed(2))
      }
      const panel = q('.qpanel')
      const tasks = q('#taskList')
      const listRect = rect(tasks)
      if (tasks) tasks.scrollTop = tasks.scrollHeight
      const last = tasks?.querySelector('.mrow:last-child')
      const lastRect = rect(last)
      const ring = q('#idRing')
      const ringRect = rect(ring)
      const hit = ringRect ? d.elementFromPoint(
        ringRect[0] + ringRect[2] / 2, ringRect[1] + ringRect[3] / 2,
      ) : null

      publish({
        size, page, vp: [w.innerWidth, w.innerHeight], ready,
        availableH: d.querySelector('main').clientHeight,
        homeVariant: home?.classList.contains('v2-home') ? 'v2' : 'v1',
        homeH: home ? home.getBoundingClientRect().height : null,
        homeOverflow: home ? home.scrollHeight - home.clientHeight : null,
        homeOverflowY: home ? w.getComputedStyle(home).overflowY : null,
        secH: sec.getBoundingClientRect().height,
        slotOverflow: slot.scrollHeight - slot.clientHeight,
        slotOverflowY: w.getComputedStyle(slot).overflowY,
        mainKids: Array.from(d.querySelector('main').children, (e) =>
          `${e.tagName.toLowerCase()}.${e.className.trim().split(/\s+/).join('.')}`),
        rules: ['.app > main', 'main > .page-slot', '.page-enter section[data-page]']
          .map((s) => [s, d.querySelectorAll(s).length]),
        btnW: q('.rbtn')?.getBoundingClientRect().width ?? null,
        fsTask: q('.mname') ? w.getComputedStyle(q('.mname')).fontSize : null,
        uVar: w.getComputedStyle(d.documentElement).getPropertyValue('--u').trim(),
        bodyOverflowX: d.body.scrollWidth - d.body.clientWidth,
        homeRect: rect(home), centerRect: rect(q('.v2-center')),
        idRect: rect(q('.idcard')), capsRect: rect(q('.caps')),
        capOverflow: Array.from(sec.querySelectorAll('.caps-row'), (e) => e.scrollWidth - e.clientWidth),
        panelRect: rect(panel), deckRect: rect(q('.deck')),
        funcRect: rect(q('.funcbar')), listRect,
        listLastVisible: !tasks || !last || (
          lastRect[1] >= listRect[1] - 1.5 &&
          lastRect[1] + lastRect[3] <= listRect[1] + listRect[3] + 1.5
        ),
        ringHit: !!(ring && hit && (ring === hit || ring.contains(hit))),
        expanded: !!q('.idcard.open'),
        liveRect: rect(q('.livecard')), videoRect: rect(video),
        videoSize: video ? [video.videoWidth, video.videoHeight] : null,
      })
      if (video?.srcObject) video.srcObject.getTracks().forEach((track) => track.stop())
    }
    tick()
  })
})()
