// ── ASCII Video Background ──────────────────────────────────
// Cycles through train videos (London → NYC → Berlin), rendering
// each as live ASCII art. Stretches to fill viewport.

(function () {
  'use strict';

  var canvas = document.getElementById('ascii-bg');
  if (!canvas) return;

  // Train videos playlist — loops continuously
  var playlist = [
    { src: 'tube-london.mp4', playbackRate: 0.4 },
    { src: 'tube-nyc.mp4',    playbackRate: 0.4 },
    { src: 'tube-berlin.mp4', playbackRate: 0.4 },
  ];
  var currentIdx = 0;

  var video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.style.display = 'none';
  document.body.appendChild(video);

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

  bufCanvas.width = asciiW;
  bufCanvas.height = asciiH;

  var animId = 0;
  var renderStarted = false;

  function loadVideo(idx) {
    currentIdx = idx % playlist.length;
    var entry = playlist[currentIdx];
    video.src = entry.src;
    video.playbackRate = entry.playbackRate;
    video.loop = false;
    video.load();
  }

  video.addEventListener('ended', function () {
    loadVideo(currentIdx + 1);
  });

  video.addEventListener('canplay', function () {
    video.play().catch(function () {});
    if (!renderStarted) {
      renderStarted = true;
      startAscii();
    }
  });

  loadVideo(0);

  function startAscii() {
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
        animId = requestAnimationFrame(renderLoop);
        return;
      }

      bufCtx.drawImage(video, 0, 0, bufCanvas.width, bufCanvas.height);
      var imgData = bufCtx.getImageData(0, 0, bufCanvas.width, bufCanvas.height);
      var pixels = imgData.data;

      var dark = document.body.getAttribute('data-theme') === 'dark';

      outCtx.clearRect(0, 0, vw, vh);
      outCtx.font = fontSize + "px 'JetBrains Mono',monospace";
      outCtx.textBaseline = 'top';

      for (var row = 0; row < asciiH; row++) {
        for (var col = 0; col < asciiW; col++) {
          var idx = (row * bufCanvas.width + col) * 4;
          var r = pixels[idx];
          var g = pixels[idx + 1];
          var b = pixels[idx + 2];

          var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

          var ci = Math.floor(lum * (charset.length - 1));
          var ch = charset[ci];
          if (ch === ' ') continue;

          var alpha = dark
            ? 0.06 + lum * 0.22
            : 0.08 + lum * 0.30;

          var color = dark
            ? 'rgba(250,250,250,' + alpha.toFixed(3) + ')'
            : 'rgba(24,24,27,' + alpha.toFixed(3) + ')';

          outCtx.fillStyle = color;
          outCtx.fillText(ch, col * charW, row * charH);
        }
      }

      animId = requestAnimationFrame(renderLoop);
    }
    renderLoop();
  }

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { location.reload(); }, 500);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { video.pause(); cancelAnimationFrame(animId); }
    else { video.play().catch(function(){}); }
  });
})();
