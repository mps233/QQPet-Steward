(() => {
  const styleId = 'qpet-window-chrome-style';
  const dragId = 'qpet-window-drag-region';
  let fullscreen = false;

  window.__qpetSetWindowFullscreen = (value) => {
    fullscreen = value === true;
    document.documentElement?.style.setProperty(
      '--desktop-top-inset',
      fullscreen ? '0px' : '28px',
    );
  };

  function installChrome() {
    const root = document.documentElement;
    if (!root) return false;
    root.dataset.qpetDesktop = 'macos';
    window.__qpetSetWindowFullscreen(fullscreen);

    if (document.head && !document.getElementById(styleId)) {
      const style = document.createElement('style');
      style.id = styleId;
      style.textContent = `
        :root[data-qpet-desktop="macos"] body {
          padding-top: max(
            env(safe-area-inset-top, 0px),
            var(--desktop-top-inset, 0px)
          ) !important;
        }
        #${dragId} {
          position: fixed;
          top: 0;
          right: 0;
          left: 0;
          height: var(--desktop-top-inset, 0px);
          z-index: 2147483647;
          background: transparent;
          user-select: none;
          -webkit-user-select: none;
          touch-action: none;
        }
      `;
      document.head.appendChild(style);
    }

    if (document.body && !document.getElementById(dragId)) {
      const dragRegion = document.createElement('div');
      dragRegion.id = dragId;
      dragRegion.setAttribute('data-tauri-drag-region', '');
      dragRegion.setAttribute('aria-hidden', 'true');
      document.body.appendChild(dragRegion);
    }

    return Boolean(document.head && document.body);
  }

  // The native initialization script can run before the HTML root exists.
  if (!installChrome()) {
    const observer = new MutationObserver(() => {
      if (installChrome()) observer.disconnect();
    });
    observer.observe(document, { childList: true, subtree: true });
  }
})();
