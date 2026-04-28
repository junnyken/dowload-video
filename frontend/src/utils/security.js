/**
 * VidGrab Security Shield
 * ========================
 * Multi-layer frontend protection:
 * 1. Block DevTools (F12, Ctrl+Shift+I/J/C, Ctrl+U)
 * 2. Disable right-click context menu
 * 3. Detect DevTools open via timing/size tricks
 * 4. Prevent text selection & drag on sensitive elements
 * 5. Anti-debugging with debugger traps
 * 6. Console log poisoning
 */

// ── 1. Block Keyboard Shortcuts ─────────────────────────────────────
function blockDevToolsKeys(e) {
  // F12
  if (e.key === 'F12' || e.keyCode === 123) {
    e.preventDefault();
    e.stopPropagation();
    return false;
  }

  // Ctrl+Shift+I (Inspector), Ctrl+Shift+J (Console), Ctrl+Shift+C (Element picker)
  if (
    (e.ctrlKey && e.shiftKey && ['I', 'i', 'J', 'j', 'C', 'c'].includes(e.key))
  ) {
    e.preventDefault();
    e.stopPropagation();
    return false;
  }

  // Ctrl+U (View Source)
  if (e.ctrlKey && (e.key === 'u' || e.key === 'U')) {
    e.preventDefault();
    e.stopPropagation();
    return false;
  }

  // Ctrl+S (Save page)
  if (e.ctrlKey && (e.key === 's' || e.key === 'S') && !e.shiftKey) {
    e.preventDefault();
    return false;
  }

  // Ctrl+Shift+K (Firefox console)
  if (e.ctrlKey && e.shiftKey && (e.key === 'K' || e.key === 'k')) {
    e.preventDefault();
    return false;
  }

  // Cmd+Option+I / Cmd+Option+J (macOS)
  if (e.metaKey && e.altKey && ['i', 'I', 'j', 'J'].includes(e.key)) {
    e.preventDefault();
    return false;
  }
}

// ── 2. Block Context Menu ───────────────────────────────────────────
function blockContextMenu(e) {
  e.preventDefault();
  return false;
}

// ── 3. DevTools Detection via window size ───────────────────────────
let devToolsOpen = false;
const THRESHOLD = 160;

function checkDevTools() {
  const widthDiff = window.outerWidth - window.innerWidth > THRESHOLD;
  const heightDiff = window.outerHeight - window.innerHeight > THRESHOLD;

  if (widthDiff || heightDiff) {
    if (!devToolsOpen) {
      devToolsOpen = true;
      onDevToolsDetected();
    }
  } else {
    devToolsOpen = false;
  }
}

function onDevToolsDetected() {
  // Overlay warning
  const overlay = document.getElementById('security-overlay');
  if (overlay) {
    overlay.style.display = 'flex';
  }
}

// ── 4. Console Poisoning ────────────────────────────────────────────
function poisonConsole() {
  const warning = [
    '%c⚠️ CẢNH BÁO BẢO MẬT!',
    'color: #FF0000; font-size: 32px; font-weight: bold; text-shadow: 2px 2px 4px rgba(0,0,0,0.3);',
  ];

  const message = [
    '%cĐây là công cụ dành cho nhà phát triển.\nNếu ai đó yêu cầu bạn dán nội dung vào đây, đó là hành vi lừa đảo.\nViệc này có thể giúp họ đánh cắp thông tin tài khoản của bạn.\n\n🔒 VidGrab Security Shield v2.0',
    'color: #FBBF24; font-size: 14px; font-family: "Be Vietnam Pro", sans-serif; line-height: 1.8;',
  ];

  try {
    console.log(...warning);
    console.log(...message);
  } catch {
    // Silently fail
  }
}

// ── 5. Anti-debugging ───────────────────────────────────────────────
function antiDebug() {
  // Periodic debugger check (slows down anyone stepping through code)
  const check = new Function('debugger');
  setInterval(() => {
    const start = performance.now();
    check();
    const diff = performance.now() - start;
    // If debugger paused, diff will be very large
    if (diff > 100) {
      onDevToolsDetected();
    }
  }, 4000);
}

// ── 6. Prevent drag & text selection on images ──────────────────────
function preventDrag() {
  document.addEventListener('dragstart', (e) => {
    if (e.target.tagName === 'IMG') {
      e.preventDefault();
    }
  });
}

// ── 7. Block iframes (clickjacking protection) ─────────────────────
function blockFraming() {
  if (window.top !== window.self) {
    // We're inside an iframe - break out
    try {
      window.top.location = window.self.location;
    } catch {
      // Cross-origin, hide content
      document.body.innerHTML = '';
    }
  }
}

// ── Main Initialization ─────────────────────────────────────────────
export function initSecurityShield() {
  // Only enable in production
  if (
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1'
  ) {
    console.log('[Security] Development mode — shield disabled');
    return;
  }

  // Block keyboard shortcuts
  document.addEventListener('keydown', blockDevToolsKeys, { capture: true });

  // Block right-click
  document.addEventListener('contextmenu', blockContextMenu);

  // DevTools size detection
  setInterval(checkDevTools, 1500);

  // Console warning
  poisonConsole();

  // Anti-debugger
  antiDebug();

  // Prevent image drag
  preventDrag();

  // Block framing
  blockFraming();

  // Add CSS to prevent text selection on sensitive areas
  const style = document.createElement('style');
  style.textContent = `
    body {
      -webkit-user-select: none;
      -moz-user-select: none;
      -ms-user-select: none;
      user-select: none;
    }
    /* Allow selection in input/textarea */
    input, textarea, [contenteditable="true"] {
      -webkit-user-select: text !important;
      -moz-user-select: text !important;
      -ms-user-select: text !important;
      user-select: text !important;
    }
    /* Prevent printing */
    @media print {
      body { display: none !important; }
    }
  `;
  document.head.appendChild(style);

  console.log('[Security] VidGrab Security Shield activated ✅');
}
