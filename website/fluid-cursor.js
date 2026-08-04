/* ========================================
   SourceCraft — Heart Cursor
   ======================================== */

(function initHeartCursor() {
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const supportsHover = window.matchMedia('(hover: hover)').matches;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (!finePointer || !supportsHover || reducedMotion) return;

  const root = document.createElement('div');
  root.className = 'heart-cursor-layer';
  root.setAttribute('aria-hidden', 'true');

  const trailLayer = document.createElement('div');
  trailLayer.className = 'heart-cursor-trails';

  const cursor = document.createElement('div');
  cursor.className = 'heart-cursor-core';
  cursor.innerHTML = [
    '<div class="heart-cursor-pulse"></div>',
    '<div class="heart-cursor-heart">❤</div>',
    '<div class="heart-cursor-glow"></div>'
  ].join('');

  root.appendChild(trailLayer);
  root.appendChild(cursor);
  document.body.appendChild(root);
  document.body.classList.add('custom-cursor-enabled', 'cursor-visible', 'heart-cursor-active');

  const style = document.createElement('style');
  style.textContent = `
    .heart-cursor-layer {
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 3000;
      opacity: 0;
      transition: opacity 180ms ease;
    }

    body.heart-cursor-active.cursor-visible .heart-cursor-layer {
      opacity: 1;
    }

    .heart-cursor-trails {
      position: absolute;
      inset: 0;
      overflow: hidden;
    }

    .heart-cursor-core {
      position: absolute;
      left: 0;
      top: 0;
      width: 44px;
      height: 44px;
      transform: translate3d(-999px, -999px, 0);
      will-change: transform;
      filter: drop-shadow(0 0 14px rgba(255, 78, 123, 0.35));
    }

    .heart-cursor-heart,
    .heart-cursor-pulse,
    .heart-cursor-glow {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .heart-cursor-heart {
      font-size: 34px;
      line-height: 1;
      color: transparent;
      background:
        radial-gradient(circle at 32% 28%, #ffd7e2 0, #ff9eb8 18%, rgba(255, 158, 184, 0.92) 28%, rgba(255, 111, 149, 0.96) 48%, #ff4e7b 68%, #d81d59 100%);
      -webkit-background-clip: text;
      background-clip: text;
      text-shadow:
        0 1px 0 rgba(255, 255, 255, 0.28),
        0 0 10px rgba(255, 78, 123, 0.42),
        0 0 24px rgba(255, 124, 156, 0.36),
        0 10px 18px rgba(216, 29, 89, 0.24);
      transform: translateY(-1px) scale(1);
      transition: transform 120ms ease;
      animation: heartCursorBeat 1.08s ease-in-out infinite;
    }

    .heart-cursor-pulse::before {
      content: '';
      width: 16px;
      height: 16px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(255, 144, 176, 0.65), rgba(255, 78, 123, 0));
      transform: scale(1.1);
      animation: heartCursorGlowBeat 1.08s ease-in-out infinite;
    }

    .heart-cursor-glow::before {
      content: '';
      width: 46px;
      height: 46px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(255, 108, 146, 0.28), rgba(255, 108, 146, 0.08) 45%, rgba(255, 108, 146, 0) 72%);
      filter: blur(5px);
      animation: heartCursorAuraBeat 1.08s ease-in-out infinite;
    }

    .heart-trail,
    .heart-burst,
    .heart-ring {
      position: absolute;
      left: 0;
      top: 0;
      will-change: transform, opacity;
      pointer-events: none;
      user-select: none;
    }

    .heart-trail,
    .heart-burst {
      color: #ff5f8f;
      line-height: 1;
      text-shadow:
        0 0 8px rgba(255, 95, 143, 0.6),
        0 0 18px rgba(255, 95, 143, 0.35);
    }

    .heart-ring {
      width: 26px;
      height: 26px;
      margin-left: -13px;
      margin-top: -13px;
      border-radius: 999px;
      border: 2px solid rgba(255, 120, 155, 0.9);
      box-shadow:
        0 0 0 4px rgba(255, 120, 155, 0.12),
        0 0 24px rgba(255, 120, 155, 0.3);
    }

    body.heart-cursor-pressed .heart-cursor-heart {
      transform: translateY(-1px) scale(1.28);
    }

    @keyframes heartCursorBeat {
      0%, 100% {
        transform: translateY(-1px) scale(1) rotate(-3deg);
      }

      14% {
        transform: translateY(-2px) scale(1.13) rotate(-1deg);
      }

      28% {
        transform: translateY(-1px) scale(0.96) rotate(-4deg);
      }

      42% {
        transform: translateY(-2px) scale(1.08) rotate(-2deg);
      }

      64% {
        transform: translateY(-1px) scale(1) rotate(-3deg);
      }
    }

    @keyframes heartCursorGlowBeat {
      0%, 100% {
        opacity: 0.58;
        transform: scale(1.05);
      }

      14% {
        opacity: 0.92;
        transform: scale(1.85);
      }

      42% {
        opacity: 0.74;
        transform: scale(1.45);
      }
    }

    @keyframes heartCursorAuraBeat {
      0%, 100% {
        opacity: 0.56;
        transform: scale(0.95);
      }

      14% {
        opacity: 0.86;
        transform: scale(1.26);
      }

      42% {
        opacity: 0.68;
        transform: scale(1.08);
      }
    }
  `;
  document.head.appendChild(style);

  const state = {
    x: window.innerWidth * 0.5,
    y: window.innerHeight * 0.5,
    mx: window.innerWidth * 0.5,
    my: window.innerHeight * 0.5,
    lastTrailX: window.innerWidth * 0.5,
    lastTrailY: window.innerHeight * 0.5,
    visible: false
  };

  let rafId = 0;
  let audioContext = null;
  let audioUnlocked = false;
  let heartbeatIntervalId = 0;

  function setVisible(visible) {
    state.visible = visible;
    document.body.classList.toggle('cursor-visible', visible);
  }

  function ensureAudioContext() {
    if (audioContext) return audioContext;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    audioContext = new AudioContextClass();
    return audioContext;
  }

  function playHeartbeatPulse(when, gainAmount, frequency, duration) {
    if (!audioContext) return;

    const osc = audioContext.createOscillator();
    const gain = audioContext.createGain();
    const filter = audioContext.createBiquadFilter();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(frequency, when);
    osc.frequency.exponentialRampToValueAtTime(Math.max(38, frequency * 0.68), when + duration);

    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(180, when);
    filter.Q.value = 0.7;

    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(gainAmount, when + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + duration);

    osc.connect(filter);
    filter.connect(gain);
    gain.connect(audioContext.destination);

    osc.start(when);
    osc.stop(when + duration + 0.02);
  }

  function playHeartbeat() {
    if (!audioUnlocked) return;
    const ctx = ensureAudioContext();
    if (!ctx) return;
    const start = ctx.currentTime + 0.01;
    playHeartbeatPulse(start, 0.045, 96, 0.11);
    playHeartbeatPulse(start + 0.19, 0.03, 72, 0.09);
  }

  function startHeartbeatLoop() {
    if (heartbeatIntervalId || !audioUnlocked) return;
    playHeartbeat();
    heartbeatIntervalId = window.setInterval(() => {
      if (!document.hidden && state.visible) {
        playHeartbeat();
      }
    }, 1080);
  }

  function unlockAudio() {
    const ctx = ensureAudioContext();
    if (!ctx) return;
    if (ctx.state === 'suspended') {
      ctx.resume();
    }
    audioUnlocked = true;
    startHeartbeatLoop();
  }

  function spawnHeart(className, x, y, options = {}) {
    const el = document.createElement('span');
    el.className = className;
    el.textContent = options.glyph || '❤';
    el.style.fontSize = `${options.size || 18}px`;
    el.style.opacity = String(options.opacity ?? 1);
    el.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%) scale(${options.scale || 1}) rotate(${options.rotate || 0}deg)`;
    if (options.color) {
      el.style.color = options.color;
    }
    trailLayer.appendChild(el);
    return el;
  }

  function animateElement(el, keyframes, timing) {
    const animation = el.animate(keyframes, timing);
    animation.addEventListener('finish', () => el.remove(), { once: true });
  }

  function spawnTrailHearts() {
    const dx = state.x - state.lastTrailX;
    const dy = state.y - state.lastTrailY;
    const distance = Math.hypot(dx, dy);
    if (distance < 8) return;

    state.lastTrailX = state.x;
    state.lastTrailY = state.y;

    const intensity = Math.min(distance / 24, 1.8);
    const count = 1 + Math.round(intensity * 2);

    for (let i = 0; i < count; i += 1) {
      const angle = Math.atan2(dy || 0.001, dx || 0.001);
      const driftX = (Math.random() - 0.5) * 26;
      const driftY = (Math.random() - 0.5) * 18;
      const startX = state.x + driftX;
      const startY = state.y + driftY;
      const endX = startX - Math.cos(angle) * (18 + Math.random() * 20);
      const endY = startY - Math.sin(angle) * (18 + Math.random() * 20) - (8 + Math.random() * 14);
      const rotate = (Math.random() - 0.5) * 30;
      const heart = spawnHeart('heart-trail', startX, startY, {
        size: 14 + Math.random() * 10,
        opacity: 0.88,
        scale: 0.9 + Math.random() * 0.45,
        rotate,
        color: Math.random() > 0.5 ? '#ff5f8f' : '#ff88a8'
      });

      animateElement(heart, [
        {
          transform: `translate3d(${startX}px, ${startY}px, 0) translate(-50%, -50%) scale(1) rotate(${rotate}deg)`,
          opacity: 0.92,
          filter: 'blur(0px)'
        },
        {
          transform: `translate3d(${endX}px, ${endY}px, 0) translate(-50%, -50%) scale(0.35) rotate(${rotate + (Math.random() - 0.5) * 80}deg)`,
          opacity: 0,
          filter: 'blur(1.5px)'
        }
      ], {
        duration: 520 + Math.random() * 240,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'forwards'
      });
    }
  }

  function spawnIdleBubbleHearts() {
    const distance = Math.hypot(state.x - state.lastTrailX, state.y - state.lastTrailY);
    if (distance >= 8) return;
    if (Math.random() > 0.08) return;

    const drift = (Math.random() - 0.5) * 18;
    const startX = state.x + drift;
    const startY = state.y + 8 + Math.random() * 12;
    const endX = startX + (Math.random() - 0.5) * 30;
    const endY = startY - (22 + Math.random() * 20);
    const rotate = (Math.random() - 0.5) * 26;
    const heart = spawnHeart('heart-trail', startX, startY, {
      size: 10 + Math.random() * 8,
      opacity: 0.62,
      scale: 0.72 + Math.random() * 0.3,
      rotate,
      color: Math.random() > 0.5 ? '#ff8cac' : '#ffc1d0'
    });

    animateElement(heart, [
      {
        transform: `translate3d(${startX}px, ${startY}px, 0) translate(-50%, -50%) scale(0.72) rotate(${rotate}deg)`,
        opacity: 0,
        filter: 'blur(0px)'
      },
      {
        transform: `translate3d(${startX + (Math.random() - 0.5) * 10}px, ${startY - 10}px, 0) translate(-50%, -50%) scale(1) rotate(${rotate + 8}deg)`,
        opacity: 0.78,
        filter: 'blur(0px)',
        offset: 0.2
      },
      {
        transform: `translate3d(${endX}px, ${endY}px, 0) translate(-50%, -50%) scale(0.38) rotate(${rotate + (Math.random() - 0.5) * 60}deg)`,
        opacity: 0,
        filter: 'blur(1.2px)'
      }
    ], {
      duration: 1650 + Math.random() * 650,
      easing: 'cubic-bezier(0.2, 0.8, 0.22, 1)',
      fill: 'forwards'
    });
  }

  function spawnClickBurst(x, y) {
    document.body.classList.add('heart-cursor-pressed');
    window.setTimeout(() => {
      document.body.classList.remove('heart-cursor-pressed');
    }, 180);

    const centerHeart = spawnHeart('heart-burst', x, y, {
      size: 30,
      opacity: 1,
      scale: 0.8,
      rotate: 0,
      color: '#ff4e7b'
    });
    animateElement(centerHeart, [
      {
        transform: `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%) scale(0.8)`,
        opacity: 1
      },
      {
        transform: `translate3d(${x}px, ${y - 6}px, 0) translate(-50%, -50%) scale(1.9)`,
        opacity: 0
      }
    ], {
      duration: 360,
      easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
      fill: 'forwards'
    });

    const count = 14;
    for (let i = 0; i < count; i += 1) {
      const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.18;
      const distance = 52 + Math.random() * 48;
      const rise = 10 + Math.random() * 24;
      const endX = x + Math.cos(angle) * distance;
      const endY = y + Math.sin(angle) * distance - rise;
      const rotate = (Math.random() - 0.5) * 60;
      const heart = spawnHeart('heart-burst', x, y, {
        size: 16 + Math.random() * 14,
        opacity: 0.98,
        scale: 0.7 + Math.random() * 0.5,
        rotate,
        color: i % 2 === 0 ? '#ff5f8f' : '#ffa0bb'
      });

      animateElement(heart, [
        {
          transform: `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%) scale(0.55) rotate(${rotate}deg)`,
          opacity: 1,
          filter: 'blur(0px)'
        },
        {
          transform: `translate3d(${endX}px, ${endY}px, 0) translate(-50%, -50%) scale(1.15) rotate(${rotate + (Math.random() - 0.5) * 120}deg)`,
          opacity: 0,
          filter: 'blur(1px)'
        }
      ], {
        duration: 620 + Math.random() * 220,
        easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
        fill: 'forwards'
      });
    }
  }

  function tick() {
    state.mx += (state.x - state.mx) * 0.28;
    state.my += (state.y - state.my) * 0.28;
    cursor.style.transform = `translate3d(${state.mx - 22}px, ${state.my - 22}px, 0)`;

    if (state.visible) {
      spawnTrailHearts();
      spawnIdleBubbleHearts();
    }

    rafId = window.requestAnimationFrame(tick);
  }

  document.addEventListener('pointermove', (event) => {
    if (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen') return;
    unlockAudio();
    state.x = event.clientX;
    state.y = event.clientY;
    if (!state.visible) {
      state.mx = state.x;
      state.my = state.y;
      state.lastTrailX = state.x;
      state.lastTrailY = state.y;
      setVisible(true);
    }
  }, { passive: true });

  document.addEventListener('pointerdown', (event) => {
    if (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen') return;
    unlockAudio();
    state.x = event.clientX;
    state.y = event.clientY;
    spawnClickBurst(event.clientX, event.clientY);
    playHeartbeat();
  }, { passive: true });

  const clearPressedState = () => {
    document.body.classList.remove('heart-cursor-pressed');
  };

  document.addEventListener('pointerup', clearPressedState);
  document.addEventListener('pointercancel', clearPressedState);

  document.addEventListener('pointerleave', () => {
    setVisible(false);
  });

  window.addEventListener('blur', () => {
    setVisible(false);
    clearPressedState();
  });

  // 特殊处理：针对下载等可能中断事件循环的操作，增加强制重置
  document.addEventListener('click', (e) => {
    if (e.target.closest('#download-btn')) {
      // 延迟一丢丢时间清除状态，确保“按下”效果能看到但不会卡死
      setTimeout(clearPressedState, 300);
    }
  }, { passive: true });

  rafId = window.requestAnimationFrame(tick);

  window.addEventListener('unload', () => {
    if (heartbeatIntervalId) {
      window.clearInterval(heartbeatIntervalId);
    }
  }, { once: true });
})();
