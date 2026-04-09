// ── ASCII Video Background ──────────────────────────────────
// Cycles through train videos (London → NYC → Berlin), rendering
// each as live ASCII art using station names as the character source.
// The text grid is STATIC — station names fill the screen once.
// The video only controls alpha/brightness, so the animation
// "plays through" the fixed text.

(function () {
  'use strict';

  var canvas = document.getElementById('ascii-bg');
  if (!canvas) return;

  // Train videos playlist — each maps to a station data file
  var playlist = [
    { src: 'tube-london.mp4', playbackRate: 0.4, dataFile: 'sample.json' },
    { src: 'tube-nyc.mp4',    playbackRate: 0.4, dataFile: 'nyc.json' },
    { src: 'tube-berlin.mp4', playbackRate: 0.4, dataFile: 'berlin.json' },
  ];
  var currentIdx = 0;

  // Pre-computed character grids per city (static, never changes per frame)
  var charGrids = {};   // dataFile → 2D array [row][col] of chars
  var currentGrid = null;

  // Clean up station names for display
  function cleanName(name) {
    return name
      .replace(/ Underground Station$/i, '')
      .replace(/ Bhf \(Berlin\)$/i, '')
      .replace(/^\s*S\+U\s+/i, '')
      .replace(/^\s*U\s+/i, '')
      .replace(/ \(Berlin\)$/i, '')
      .toUpperCase();
  }

  // Build a static character grid from station names
  function buildGrid(names, cols, rows) {
    // Shuffle for visual variety
    var shuffled = names.slice();
    for (var j = shuffled.length - 1; j > 0; j--) {
      var k = Math.floor(Math.random() * (j + 1));
      var tmp = shuffled[j]; shuffled[j] = shuffled[k]; shuffled[k] = tmp;
    }

    // Build a long text string with dot separators
    var text = shuffled.join(' \u00B7 ');
    while (text.length < cols * rows + 1000) text = text + ' \u00B7 ' + shuffled.join(' \u00B7 ');

    // Fill grid row by row
    var grid = [];
    var ti = 0;
    for (var r = 0; r < rows; r++) {
      var row = [];
      for (var c = 0; c < cols; c++) {
        row.push(text[ti % text.length]);
        ti++;
      }
      grid.push(row);
    }
    return grid;
  }

  // Load station names from a JSON file, then build the grid
  function loadStationNames(dataFile) {
    if (charGrids[dataFile] !== undefined) return;
    charGrids[dataFile] = null; // mark as loading

    var xhr = new XMLHttpRequest();
    xhr.open('GET', dataFile, true);
    xhr.onload = function () {
      if (xhr.status === 200) {
        try {
          var data = JSON.parse(xhr.responseText);
          var names = [];
          if (data.stations) {
            var keys = Object.keys(data.stations);
            for (var i = 0; i < keys.length; i++) {
              names.push(cleanName(data.stations[keys[i]].name));
            }
          }
          if (names.length > 0) {
            var grid = buildGrid(names, asciiW, asciiH);
            charGrids[dataFile] = grid;
            if (playlist[currentIdx].dataFile === dataFile) {
              currentGrid = grid;
            }
          }
        } catch (e) {
          charGrids[dataFile] = null;
        }
      }
    };
    xhr.send();
  }

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

  // Preload all station texts (needs asciiW/asciiH to be set first)
  for (var p = 0; p < playlist.length; p++) {
    loadStationNames(playlist[p].dataFile);
  }

  function loadVideo(idx) {
    currentIdx = idx % playlist.length;
    var entry = playlist[currentIdx];
    video.src = entry.src;
    video.playbackRate = entry.playbackRate;
    video.loop = false;
    video.load();
    // Switch to this city's grid
    currentGrid = charGrids[entry.dataFile] || null;
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

    var lumThreshold = 0.04;

    function renderLoop() {
      if (video.paused || video.ended) {
        animId = requestAnimationFrame(renderLoop);
        return;
      }

      bufCtx.drawImage(video, 0, 0, bufCanvas.width, bufCanvas.height);
      var imgData = bufCtx.getImageData(0, 0, bufCanvas.width, bufCanvas.height);
      var pixels = imgData.data;

      var dark = document.body.getAttribute('data-theme') === 'dark';
      var grid = currentGrid || charGrids[playlist[currentIdx].dataFile];

      outCtx.clearRect(0, 0, vw, vh);
      outCtx.font = fontSize + "px 'JetBrains Mono',monospace";
      outCtx.textBaseline = 'top';

      for (var row = 0; row < asciiH; row++) {
        for (var col = 0; col < asciiW; col++) {
          var idx = (row * bufCanvas.width + col) * 4;
          var r = pixels[idx], g = pixels[idx + 1], b = pixels[idx + 2];
          var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

          if (lum < lumThreshold) continue;

          // Get character from the static grid, or fallback
          var ch;
          if (grid && grid[row]) {
            ch = grid[row][col];
          } else {
            // Fallback: classic luminance-mapped charset
            var charset = ' .,:;i1tfLCG08@';
            var ci = Math.floor(lum * (charset.length - 1));
            ch = charset[ci];
            if (ch === ' ') continue;
          }

          var alpha = dark
            ? 0.12 + lum * 0.38
            : 0.18 + lum * 0.55;

          outCtx.fillStyle = dark
            ? 'rgba(250,250,250,' + alpha.toFixed(3) + ')'
            : 'rgba(24,24,27,' + alpha.toFixed(3) + ')';
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
