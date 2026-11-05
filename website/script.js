/* ========================================
   SourceCraft — Interactions
   ======================================== */

document.addEventListener('DOMContentLoaded', () => {

  const isEnglishUI = () => {
    const fromApi = window.I18N && typeof window.I18N.getLang === 'function' ? window.I18N.getLang() : '';
    const lang = fromApi || document.documentElement.getAttribute('lang') || 'en';
    return String(lang).toLowerCase().indexOf('en') === 0;
  };

  const applyReleaseInfo = (release) => {
    if (!release || typeof release !== 'object') {
      return;
    }

    const version = String(release.version || '').trim();
    const downloadUrl = String(release.download_url || '').trim();
    const fileSize = String(release.file_size || '').trim();

    // i18n 表里是「按版本号写死」的 key（如 'v1.1.156 · 最新版本'），换版本就会漏译文、
    // 英文官网露出中文。故未命中且当前是英文界面时用英文兜底 —— 版本号变化从此无需改 i18n 表。
    const renderLine = (zhText, enText) => {
      const translated = window.I18N ? window.I18N.t(zhText) : zhText;
      return (translated === zhText && isEnglishUI()) ? enText : translated;
    };

    const renderVersion = () => {
      if (!version) return;
      const heroVersion = document.getElementById('hero-version-text');
      if (heroVersion) {
        heroVersion.textContent = renderLine(`v${version} · 最新版本`, `v${version} · Latest version`);
      }
      const downloadVersion = document.getElementById('download-version-text');
      if (downloadVersion) {
        downloadVersion.textContent = renderLine(
          `当前版本 v${version} · 适用于 Windows 系统`,
          `Version v${version} · For Windows`,
        );
      }
    };

    if (version) {
      renderVersion();
    }

    if (downloadUrl) {
      const downloadBtn = document.getElementById('download-btn');
      if (downloadBtn) {
        downloadBtn.href = downloadUrl;
      }
    }

    if (fileSize) {
      const packageSize = document.getElementById('package-size-text');
      if (packageSize) {
        packageSize.textContent = fileSize;
      }
    }
  };

  // ---------- 发行信息：中英文统一使用国内版 version.json ----------
  // 海外版已下架，中英文界面共用同一份发行信息与下载链接。
  let activeRelease = null;

  const pickRelease = () => {
    const next = activeRelease;
    if (!next) {
      return;
    }
    // 每次重新套用：切换语言时版本号文案（"v… · 最新版本"）也需要跟着重渲染
    applyReleaseInfo(next);
  };

  const loadRelease = (path) => fetch(path, { cache: 'no-store' })
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      if (!data || typeof data !== 'object' || !String(data.download_url || '').trim()) {
        // 注意：本服务器对未知路径会回退 index.html（200 + text/html），
        // 所以"取到了但不是发行信息"不是异常，必须显式忽略，绝不能把 HTML 当成发行信息用。
        console.warn(`${path} 内容不是有效的发行信息，已忽略`);
        return;
      }
      activeRelease = data;
    })
    .catch(error => {
      console.warn(`${path} 加载失败:`, error);
    });

  loadRelease('./version.json').then(() => {
    pickRelease();
    document.addEventListener('i18n:changed', pickRelease);
  });

  // ---------- Premium cursor system ----------
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const supportsHover = window.matchMedia('(hover: hover)').matches;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (finePointer && supportsHover) {
    const particleCanvas = document.getElementById('cursor-particles');
    const cursorDot = document.getElementById('cursor-dot');
    const cursorRing = document.getElementById('cursor-ring');
    const cursorAura = document.getElementById('cursor-aura');
    const cursorLabel = document.getElementById('cursor-label');

    if (particleCanvas && cursorDot && cursorRing && cursorAura && cursorLabel) {
      document.body.classList.add('custom-cursor-enabled');
      const ctx = particleCanvas.getContext('2d');
      const particles = [];
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      const state = {
        targetX: window.innerWidth / 2,
        targetY: window.innerHeight / 2,
        ringX: window.innerWidth / 2,
        ringY: window.innerHeight / 2,
        auraX: window.innerWidth / 2,
        auraY: window.innerHeight / 2,
        lastX: window.innerWidth / 2,
        lastY: window.innerHeight / 2,
        velocityX: 0,
        velocityY: 0,
        speed: 0,
        visible: false,
        hovering: false,
        pressed: false
      };

      const resizeParticleCanvas = () => {
        particleCanvas.width = Math.floor(window.innerWidth * dpr);
        particleCanvas.height = Math.floor(window.innerHeight * dpr);
        particleCanvas.style.width = `${window.innerWidth}px`;
        particleCanvas.style.height = `${window.innerHeight}px`;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      };

      const spawnParticle = (x, y, options = {}) => {
        const maxLife = options.life ?? (18 + Math.random() * 20);
        const angle = options.angle ?? Math.random() * Math.PI * 2;
        const speed = options.speed ?? (0.4 + Math.random() * 1.3);

        particles.push({
          x,
          y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          life: maxLife,
          maxLife,
          size: options.size ?? (1 + Math.random() * 2.8),
          hue: options.hue ?? (state.hovering ? 195 + Math.random() * 55 : 230 + Math.random() * 35),
          alpha: options.alpha ?? (state.hovering ? 0.85 : 0.48),
          drag: options.drag ?? 0.94,
          stretch: options.stretch ?? (0.7 + Math.random() * 1.8)
        });
      };

      const burstParticles = (x, y, count, speed, directionAngle = null) => {
        for (let i = 0; i < count; i += 1) {
          const spreadBase = directionAngle === null
            ? (Math.PI * 2 * i) / count
            : directionAngle + (Math.random() - 0.5) * 1.35;
          spawnParticle(x, y, {
            angle: spreadBase + Math.random() * 0.24,
            speed: speed * (0.65 + Math.random() * 0.75),
            life: 20 + Math.random() * 18,
            size: 1.6 + Math.random() * 3.4,
            alpha: 0.92,
            drag: 0.92,
            hue: 185 + Math.random() * 80,
            stretch: 1.2 + Math.random() * 2.6
          });
        }
      };

      const sprayParticles = (x, y, intensity = 1) => {
        const moveAngle = Math.atan2(state.velocityY || 0.001, state.velocityX || 0.001);
        const oppositeAngle = moveAngle + Math.PI;
        const sidewaysAngle = moveAngle + (Math.random() > 0.5 ? Math.PI / 2 : -Math.PI / 2);
        const streakCount = Math.max(2, Math.round(2 + intensity * 3));

        for (let i = 0; i < streakCount; i += 1) {
          spawnParticle(
            x + (Math.random() - 0.5) * 10,
            y + (Math.random() - 0.5) * 10,
            {
              angle: oppositeAngle + (Math.random() - 0.5) * 0.7,
              speed: 0.8 + Math.random() * (1.4 + intensity * 1.2),
              life: 14 + Math.random() * 18,
              size: 1.2 + Math.random() * 2.8,
              alpha: 0.5 + Math.random() * 0.28,
              drag: 0.9,
              hue: state.hovering ? 185 + Math.random() * 60 : 220 + Math.random() * 35,
              stretch: 1.8 + Math.random() * 4.2
            }
          );
        }

        if (intensity > 0.45) {
          spawnParticle(x, y, {
            angle: sidewaysAngle + (Math.random() - 0.5) * 0.8,
            speed: 0.7 + Math.random() * 1.8,
            life: 12 + Math.random() * 12,
            size: 1 + Math.random() * 2.2,
            alpha: 0.34 + Math.random() * 0.24,
            drag: 0.91,
            hue: 190 + Math.random() * 70,
            stretch: 1.5 + Math.random() * 2.5
          });
        }
      };

      const interactiveSelectors = [
        'a',
        'button',
        '.btn',
        '.feature-card',

        '.download-box',
        '.floating-contact-btn',
        '.showcase-image-shell',
        '.hero-liquid-frame',
        '.navbar-brand'
      ].join(', ');

      const labelByContext = [
        { selector: '#download-btn', label: '下载' },
        { selector: '.navbar-links a, .btn, .floating-contact-btn', label: '打开' },
        { selector: '.feature-card', label: '功能' },

        { selector: '.download-box', label: '获取' },
        { selector: '.showcase-image-shell, .hero-liquid-frame', label: '预览' },
        { selector: '.navbar-brand', label: '首页' }
      ];

      const setCursorVisible = (visible) => {
        state.visible = visible;
        document.body.classList.toggle('cursor-visible', visible);
      };

      const setHoverState = (target) => {
        const interactiveTarget = target?.closest(interactiveSelectors);
        state.hovering = Boolean(interactiveTarget);
        document.body.classList.toggle('cursor-hover', state.hovering);

        if (!interactiveTarget) {
          cursorLabel.textContent = '查看';
          return;
        }

        const matched = labelByContext.find(item => interactiveTarget.matches(item.selector));
        cursorLabel.textContent = matched?.label || interactiveTarget.dataset.cursor || '查看';
      };

      const animateCursor = () => {
        const ringEase = reducedMotion ? 1 : 0.16;
        const auraEase = reducedMotion ? 1 : 0.11;

        state.ringX += (state.targetX - state.ringX) * ringEase;
        state.ringY += (state.targetY - state.ringY) * ringEase;
        state.auraX += (state.targetX - state.auraX) * auraEase;
        state.auraY += (state.targetY - state.auraY) * auraEase;

        ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

        if (state.visible) {
          const intensity = Math.min(state.speed / 18, 1.25) + (state.hovering ? 0.22 : 0);
          sprayParticles(state.targetX, state.targetY, intensity);
        }

        for (let i = particles.length - 1; i >= 0; i -= 1) {
          const particle = particles[i];
          particle.x += particle.vx;
          particle.y += particle.vy;
          particle.vx *= particle.drag;
          particle.vy *= particle.drag;
          particle.life -= 1;

          if (particle.life <= 0) {
            particles.splice(i, 1);
            continue;
          }

          const progress = particle.life / particle.maxLife;
          const radius = particle.size * (0.55 + progress * 0.9);
          const angle = Math.atan2(particle.vy, particle.vx);
          const stretch = 1 + (1 - progress) * particle.stretch;
          const gradient = ctx.createRadialGradient(
            particle.x,
            particle.y,
            0,
            particle.x,
            particle.y,
            radius * 4
          );
          gradient.addColorStop(0, `hsla(${particle.hue}, 100%, 88%, ${particle.alpha * progress})`);
          gradient.addColorStop(0.45, `hsla(${particle.hue}, 96%, 68%, ${(particle.alpha * progress) * 0.45})`);
          gradient.addColorStop(1, `hsla(${particle.hue}, 96%, 60%, 0)`);

          ctx.save();
          ctx.translate(particle.x, particle.y);
          ctx.rotate(angle);
          ctx.scale(stretch, 1);
          ctx.beginPath();
          ctx.fillStyle = gradient;
          ctx.arc(0, 0, radius * 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }

        cursorDot.style.transform = `translate3d(${state.targetX}px, ${state.targetY}px, 0) translate(-50%, -50%)`;
        cursorRing.style.transform = `translate3d(${state.ringX}px, ${state.ringY}px, 0) translate(-50%, -50%)`;
        cursorAura.style.transform = `translate3d(${state.auraX}px, ${state.auraY}px, 0) translate(-50%, -50%)`;
        cursorLabel.style.transform = `translate3d(${state.ringX + 22}px, ${state.ringY - 22}px, 0) translate(0, -50%)`;

        requestAnimationFrame(animateCursor);
      };

      document.addEventListener('pointermove', (event) => {
        if (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen') {
          return;
        }

        state.velocityX = event.clientX - state.lastX;
        state.velocityY = event.clientY - state.lastY;
        state.speed = Math.hypot(state.velocityX, state.velocityY);
        state.lastX = event.clientX;
        state.lastY = event.clientY;
        state.targetX = event.clientX;
        state.targetY = event.clientY;

        if (!state.visible) {
          state.ringX = event.clientX;
          state.ringY = event.clientY;
          state.auraX = event.clientX;
          state.auraY = event.clientY;
          state.lastX = event.clientX;
          state.lastY = event.clientY;
          setCursorVisible(true);
          burstParticles(event.clientX, event.clientY, 10, 1.6);
        }

        setHoverState(event.target);
      }, { passive: true });

      document.addEventListener('pointerdown', () => {
        state.pressed = true;
        document.body.classList.add('cursor-pressed', 'cursor-clicking');
        const directionAngle = Math.atan2(state.velocityY || 0.001, state.velocityX || 0.001) + Math.PI;
        burstParticles(
          state.targetX,
          state.targetY,
          state.hovering ? 20 : 14,
          state.hovering ? 2.8 : 2.1,
          directionAngle
        );
        burstParticles(
          state.targetX,
          state.targetY,
          state.hovering ? 8 : 6,
          state.hovering ? 1.8 : 1.35,
          directionAngle + Math.PI / 2
        );
        window.setTimeout(() => {
          document.body.classList.remove('cursor-clicking');
        }, 520);
      });

      document.addEventListener('pointerup', () => {
        state.pressed = false;
        document.body.classList.remove('cursor-pressed');
      });

      document.addEventListener('pointerleave', () => {
        setCursorVisible(false);
        document.body.classList.remove('cursor-hover', 'cursor-pressed');
      });

      window.addEventListener('blur', () => {
        setCursorVisible(false);
        document.body.classList.remove('cursor-hover', 'cursor-pressed');
      });

      window.addEventListener('resize', resizeParticleCanvas);
      resizeParticleCanvas();
      animateCursor();
    }
  }

  // ---------- Navbar scroll effect ----------
  const navbar = document.getElementById('navbar');
  const onScroll = () => {
    navbar.classList.toggle('scrolled', window.scrollY > 40);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // ---------- Mobile menu toggle ----------
  const mobileBtn = document.getElementById('mobile-menu-btn');
  const navLinks = document.getElementById('nav-links');

  if (mobileBtn && navLinks) {
    mobileBtn.addEventListener('click', () => {
      navLinks.classList.toggle('active');
      mobileBtn.textContent = navLinks.classList.contains('active') ? '✕' : '☰';
    });

    // Close menu when a link is clicked
    navLinks.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        navLinks.classList.remove('active');
        mobileBtn.textContent = '☰';
      });
    });
  }

  // ---------- Smooth scroll for anchor links ----------
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(anchor.getAttribute('href'));
      if (target) {
        const offset = 80; // navbar height
        const top = target.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top, behavior: 'smooth' });
      }
    });
  });

  // ---------- Scroll reveal animations ----------
  const revealElements = document.querySelectorAll('.reveal');

  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        revealObserver.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.12,
    rootMargin: '0px 0px -40px 0px'
  });

  revealElements.forEach(el => revealObserver.observe(el));

  // ---------- Hero image fallback ----------
  const heroImg = document.getElementById('hero-img');
  const showcaseImg = document.getElementById('showcase-img');

  function setPlaceholder(img, label) {
    if (!img) return;
    img.onerror = () => {
      // Create a gradient placeholder
      const canvas = document.createElement('canvas');
      canvas.width = 800;
      canvas.height = 500;
      const ctx = canvas.getContext('2d');

      // Background
      const grad = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
      grad.addColorStop(0, '#1e1b4b');
      grad.addColorStop(0.5, '#312e81');
      grad.addColorStop(1, '#0f172a');
      ctx.fillStyle = grad;
      ctx.roundRect(0, 0, canvas.width, canvas.height, 20);
      ctx.fill();

      // Title bar
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.fillRect(0, 0, canvas.width, 40);

      // Window controls
      const dots = ['#ef4444', '#f59e0b', '#22c55e'];
      dots.forEach((c, i) => {
        ctx.beginPath();
        ctx.arc(20 + i * 22, 20, 6, 0, Math.PI * 2);
        ctx.fillStyle = c;
        ctx.fill();
      });

      // Sidebar
      ctx.fillStyle = 'rgba(255,255,255,0.03)';
      ctx.fillRect(0, 40, 180, canvas.height);

      // Sidebar items
      for (let i = 0; i < 6; i++) {
        ctx.fillStyle = i === 0 ? 'rgba(99,102,241,0.25)' : 'rgba(255,255,255,0.04)';
        ctx.beginPath();
        ctx.roundRect(12, 56 + i * 44, 156, 34, 6);
        ctx.fill();
        ctx.fillStyle = i === 0 ? 'rgba(167,139,250,0.7)' : 'rgba(255,255,255,0.15)';
        ctx.fillRect(24, 68 + i * 44, 60 + Math.random() * 60, 10);
      }

      // Main content area
      // URL bar
      ctx.fillStyle = 'rgba(255,255,255,0.05)';
      ctx.beginPath();
      ctx.roundRect(196, 54, canvas.width - 212, 32, 6);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.12)';
      ctx.fillRect(210, 66, 200, 8);

      // Content blocks
      ctx.fillStyle = 'rgba(255,255,255,0.04)';
      ctx.beginPath();
      ctx.roundRect(196, 100, canvas.width - 212, 160, 8);
      ctx.fill();

      // Progress bar
      ctx.fillStyle = 'rgba(99,102,241,0.3)';
      ctx.beginPath();
      ctx.roundRect(210, 280, canvas.width - 240, 8, 4);
      ctx.fill();
      ctx.fillStyle = 'rgba(99,102,241,0.8)';
      ctx.beginPath();
      ctx.roundRect(210, 280, (canvas.width - 240) * 0.65, 8, 4);
      ctx.fill();

      // Bottom cards
      for (let i = 0; i < 3; i++) {
        const cardW = (canvas.width - 212 - 24) / 3;
        ctx.fillStyle = 'rgba(255,255,255,0.03)';
        ctx.beginPath();
        ctx.roundRect(196 + i * (cardW + 12), 310, cardW, 120, 8);
        ctx.fill();
      }

      // Text: SourceCraft
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.font = 'bold 40px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('SourceCraft', canvas.width / 2 + 90, 200);

      img.src = canvas.toDataURL('image/png');
    };
  }

  setPlaceholder(heroImg, 'hero');
  setPlaceholder(showcaseImg, 'showcase');

  // Trigger error check for missing images
  if (heroImg && !heroImg.complete) {
    heroImg.addEventListener('load', () => { }, { once: true });
  }
  if (showcaseImg && !showcaseImg.complete) {
    showcaseImg.addEventListener('load', () => { }, { once: true });
  }

  // ---------- Image heart bubbles ----------
  const bubbleScenes = Array.from(document.querySelectorAll('.hero-liquid-frame, .showcase-image-shell'))
    .map(scene => {
      const layer = scene.querySelector('.heart-bubble-layer');
      if (!layer) return null;

      const bubbles = Array.from(layer.querySelectorAll('.heart-bubble')).map((bubble, index) => ({
        el: bubble,
        xRatio: [0.12, 0.28, 0.52, 0.74, 0.88][index] ?? (0.14 + index * 0.16),
        phase: Math.random(),
        speed: 0.045 + Math.random() * 0.018,
        sway: 18 + Math.random() * 16,
        scale: 0.78 + Math.random() * 0.2,
        active: 0,
        burstUntil: 0,
        interactX: 0,
        interactY: 0
      }));

      return {
        scene,
        layer,
        bubbles,
        mouseX: 0,
        mouseY: 0,
        mouseActive: false,
        lastPopAt: 0
      };
    })
    .filter(Boolean);

  if (bubbleScenes.length > 0) {
    const spawnBubblePop = (item, x, y) => {
      const heart = document.createElement('span');
      const offsetX = (Math.random() - 0.5) * 34;
      const offsetY = 10 + Math.random() * 14;
      const size = 22 + Math.random() * 16;

      heart.className = 'heart-bubble-pop';
      heart.textContent = '❤';
      heart.style.left = `${x + offsetX}px`;
      heart.style.top = `${y + offsetY}px`;
      heart.style.fontSize = `${size}px`;
      item.layer.appendChild(heart);

      window.setTimeout(() => {
        heart.remove();
      }, 980);
    };

    const spawnHeartShardBurst = (item, bubble, x, y) => {
      const shardCount = 6 + Math.floor(Math.random() * 3);
      const now = performance.now();
      bubble.burstUntil = now + 880;
      bubble.phase = Math.random();

      for (let i = 0; i < shardCount; i += 1) {
        const shard = document.createElement('span');
        const angle = (-Math.PI * 0.95) + (Math.PI * 1.9 * (i / Math.max(shardCount - 1, 1))) + ((Math.random() - 0.5) * 0.3);
        const distance = 30 + Math.random() * 44;
        const size = 11 + Math.random() * 10;
        const driftX = Math.cos(angle) * distance;
        const driftY = Math.sin(angle) * distance - 10 - Math.random() * 16;

        shard.className = 'heart-bubble-shard';
        shard.textContent = '❤';
        shard.style.left = `${x}px`;
        shard.style.top = `${y}px`;
        shard.style.fontSize = `${size}px`;
        shard.style.setProperty('--tx', '0px');
        shard.style.setProperty('--ty', '0px');
        shard.style.setProperty('--dx', `${driftX}px`);
        shard.style.setProperty('--dy', `${driftY}px`);
        shard.style.setProperty('--rot-start', `${-18 + Math.random() * 36}deg`);
        shard.style.setProperty('--rot-end', `${-120 + Math.random() * 240}deg`);
        item.layer.appendChild(shard);

        window.setTimeout(() => {
          shard.remove();
        }, 860);
      }
    };

    bubbleScenes.forEach((item) => {
      item.bubbles.forEach((bubble) => {
        bubble.el.addEventListener('click', (event) => {
          event.stopPropagation();

          const now = performance.now();
          if (bubble.burstUntil > now) return;

          const rect = item.scene.getBoundingClientRect();
          const x = event.clientX - rect.left;
          const y = event.clientY - rect.top;
          spawnHeartShardBurst(item, bubble, x, y);
        });
      });

      item.scene.addEventListener('pointermove', (event) => {
        const rect = item.scene.getBoundingClientRect();
        item.mouseX = event.clientX - rect.left;
        item.mouseY = event.clientY - rect.top;
        item.mouseActive = true;

        const now = performance.now();
        if (now - item.lastPopAt > 140) {
          item.lastPopAt = now;
          spawnBubblePop(item, item.mouseX, item.mouseY);
        }
      }, { passive: true });

      item.scene.addEventListener('pointerleave', () => {
        item.mouseActive = false;
      });
    });

    const animateBubbleScenes = (time) => {
      bubbleScenes.forEach((item) => {
        const rect = item.scene.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;
        const liftRange = height + 260;

        item.bubbles.forEach((bubble, index) => {
          const now = performance.now();
          const t = (time * 0.001 * bubble.speed + bubble.phase) % 1;
          const eased = 1 - ((1 - t) * (1 - t));
          const baseX = width * bubble.xRatio;
          const baseY = height + 80 - eased * liftRange;
          const sway = Math.sin((t * Math.PI * 2.2) + (index * 0.85)) * bubble.sway;
          const drift = Math.cos((t * Math.PI * 3.1) + bubble.phase * 6) * 8;
          let pushX = 0;
          let pushY = 0;
          let active = 0;

          if (item.mouseActive) {
            const dx = baseX + sway - item.mouseX;
            const dy = baseY - item.mouseY;
            const distance = Math.hypot(dx, dy);
            const radius = 300;

            if (distance < radius) {
              const force = ((radius - distance) / radius) ** 1.2;
              pushX = (dx / Math.max(distance, 1)) * force * 168;
              pushY = (dy / Math.max(distance, 1)) * force * 112 - force * 8;
              active = force;
            }
          }

          bubble.interactX += (pushX - bubble.interactX) * 0.34;
          bubble.interactY += (pushY - bubble.interactY) * 0.34;
          bubble.active += (active - bubble.active) * 0.22;

          const opacity = t < 0.12
            ? t / 0.12 * 0.72
            : t > 0.84
              ? (1 - t) / 0.16 * 0.72
              : 0.72;
          const scale = bubble.scale + eased * 0.34 + bubble.active * 0.26;
          const rotate = Math.sin((t * Math.PI * 2) + bubble.phase * 5) * (7 + bubble.active * 8);
          const isBursting = bubble.burstUntil > now;

          bubble.el.style.opacity = isBursting ? '0' : `${Math.max(opacity, 0)}`;
          bubble.el.classList.toggle('is-engaged', bubble.active > 0.12);
          bubble.el.classList.toggle('is-popping', isBursting);
          bubble.el.style.transform = `translate3d(${baseX + sway + drift + bubble.interactX}px, ${baseY + bubble.interactY}px, 0) translate(-50%, -50%) scale(${scale}) rotate(${rotate}deg)`;
        });
      });

      requestAnimationFrame(animateBubbleScenes);
    };

    requestAnimationFrame(animateBubbleScenes);
  }

  // ---------- Floating contact toggle ----------
  const contactBtn = document.getElementById('contact-btn');
  const contactPanel = document.getElementById('contact-panel');

  if (contactBtn && contactPanel) {
    contactBtn.addEventListener('click', () => {
      contactPanel.classList.toggle('active');
    });

    // Close panel when clicking outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.floating-contact')) {
        contactPanel.classList.remove('active');
      }
    });
  }
});
