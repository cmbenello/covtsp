// ── ASCII Video Background ──────────────────────────────────
// Uses aalib.js to convert a tube train video to live ASCII art.
// Stretches video to fill viewport via intermediary canvas.
// Self-contained apart from aalib.js dependency.

(function () {
  'use strict';

  var canvas = document.getElementById('ascii-bg');
  if (!canvas) return;

  // Create a hidden video element
  var video = document.createElement('video');
  video.src = 'tube-bg.mp4';
  video.loop = true;
  video.muted = true;
  video.playsInline = true;
  video.playbackRate = 0.4;
  video.style.display = 'none';
  document.body.appendChild(video);

  // Intermediary canvas to stretch video to viewport aspect ratio
  var bufCanvas = document.createElement('canvas');
  var bufCtx = bufCanvas.getContext('2d');
  bufCanvas.style.display = 'none';
  document.body.appendChild(bufCanvas);

  // ASCII grid sizing
  var charW = 6;
  var charH = 10;
  var fontSize = 8;
  if (window.innerWidth < 640) { charW = 8; charH = 13; fontSize = 10; }

  var vw = window.innerWidth;
  var vh = window.innerHeight;
  var asciiW = Math.ceil(vw / charW);
  var asciiH = Math.ceil(vh / charH);

  // Set buffer canvas to match ASCII grid pixel dimensions
  bufCanvas.width = asciiW;
  bufCanvas.height = asciiH;

  var animId = 0;
  var started = false;

  // Start video and begin ASCII rendering
  video.addEventListener('canplay', function onCanPlay() {
    video.removeEventListener('canplay', onCanPlay);
    video.play().catch(function () {});
    if (!started) { started = true; startAscii(); }
  });
  video.load();

  function startAscii() {
    if (typeof aalib === 'undefined') return;

    var isDark = document.body.getAttribute('data-theme') === 'dark';

    // Draw stretched video frames to buffer, then feed to aalib
    function pumpFrame() {
      if (video.paused || video.ended) {
        animId = requestAnimationFrame(pumpFrame);
        return;
      }
      // Draw video stretched to fill buffer (covers full viewport ratio)
      bufCtx.drawImage(video, 0, 0, bufCanvas.width, bufCanvas.height);
      animId = requestAnimationFrame(pumpFrame);
    }
    pumpFrame();

    // Use aalib's imageData reader on the buffer canvas
    // Since aalib's video reader uses the video's native size,
    // we'll use a continuous render loop instead
    var outCanvas = canvas;
    var dpr = window.devicePixelRatio || 1;
    outCanvas.width = vw * dpr;
    outCanvas.height = vh * dpr;
    outCanvas.style.width = vw + 'px';
    outCanvas.style.height = vh + 'px';
    var outCtx = outCanvas.getContext('2d');
    outCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

    var charset = ' .,:;i1tfLCG08@';

    function renderLoop() {
      if (video.paused || video.ended) {
        requestAnimationFrame(renderLoop);
        return;
      }

      // Draw video to buffer, covering full viewport ratio
      bufCtx.drawImage(video, 0, 0, bufCanvas.width, bufCanvas.height);
      var imgData = bufCtx.getImageData(0, 0, bufCanvas.width, bufCanvas.height);
      var pixels = imgData.data;

      // Check theme each frame
      var dark = document.body.getAttribute('data-theme') === 'dark';

      // Clear output
      outCtx.clearRect(0, 0, vw, vh);
      outCtx.font = fontSize + "px 'JetBrains Mono',monospace";
      outCtx.textBaseline = 'top';

      for (var row = 0; row < asciiH; row++) {
        for (var col = 0; col < asciiW; col++) {
          var idx = (row * bufCanvas.width + col) * 4;
          var r = pixels[idx];
          var g = pixels[idx + 1];
          var b = pixels[idx + 2];

          // Luminance
          var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

          // Map to character
          var ci = Math.floor(lum * (charset.length - 1));
          var ch = charset[ci];
          if (ch === ' ') continue;

          // Alpha based on brightness — brighter parts more visible
          var alpha = dark
            ? 0.04 + lum * 0.16
            : 0.03 + lum * 0.14;

          var color = dark
            ? 'rgba(250,250,250,' + alpha.toFixed(3) + ')'
            : 'rgba(24,24,27,' + alpha.toFixed(3) + ')';

          outCtx.fillStyle = color;
          outCtx.fillText(ch, col * charW, row * charH);
        }
      }

      requestAnimationFrame(renderLoop);
    }
    renderLoop();
  }

  // Handle resize
  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { location.reload(); }, 500);
  });

  // Pause when hidden
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { video.pause(); cancelAnimationFrame(animId); }
    else { video.play().catch(function(){}); }
  });
})();
