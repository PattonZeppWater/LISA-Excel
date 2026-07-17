import { useRef, useEffect } from 'react'

const VW = 160, VH = 100, SCALE = 4

const C = {
  GROUND:      '#1c2e1a',
  WALL:        '#3e4e3c',
  CONCRETE:    '#4a5256',
  WATER_RAW:   '#2a4838',
  WATER_1:     '#1a3048',
  WATER_2:     '#1a4a5a',
  WATER_CLEAN: '#0a8a9a',
  WATER_OUT:   '#0acab0',
  PIPE:        '#1a1a2a',
  MEMBRANE:    '#2a8090',
  FIBRE:       '#6acac8',
  CHLORINE:    '#c8b020',
  PROBE:       '#e0d030',
}

// ── Draw helpers ──────────────────────────────────────────────────────────────

function px(ctx, x, y, w, h, color) {
  ctx.fillStyle = color
  ctx.fillRect(Math.round(x) * SCALE, Math.round(y) * SCALE, Math.round(w) * SCALE, Math.round(h) * SCALE)
}

function circ(ctx, cx, cy, r, color) {
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(cx * SCALE, cy * SCALE, r * SCALE, 0, Math.PI * 2)
  ctx.fill()
}

// ── Flow path: river → intake → caisson → raw WPS → top CMF train → permeate
//    header → CT serpentine → clear well → treated WPS → distribution
const WAYPOINTS = [
  [4, 50],   [12, 50],  [22, 50],  [38, 50],  [46, 50],
  [46, 28],  [54, 28],  [98, 28],  [102, 28],
  [102, 50], [108, 50],
  [114, 44], [120, 56], [124, 50],
  [136, 50], [148, 50], [156, 50], [160, 50],
]

const SEG_LENS = []
let PATH_LEN = 0
for (let i = 1; i < WAYPOINTS.length; i++) {
  const dx = WAYPOINTS[i][0] - WAYPOINTS[i - 1][0]
  const dy = WAYPOINTS[i][1] - WAYPOINTS[i - 1][1]
  const l  = Math.sqrt(dx * dx + dy * dy)
  SEG_LENS.push(l)
  PATH_LEN += l
}

function posAtT(t) {
  let rem = ((t % PATH_LEN) + PATH_LEN) % PATH_LEN
  for (let i = 0; i < SEG_LENS.length; i++) {
    if (rem <= SEG_LENS[i]) {
      const f = rem / SEG_LENS[i]
      return [
        WAYPOINTS[i][0] + f * (WAYPOINTS[i + 1][0] - WAYPOINTS[i][0]),
        WAYPOINTS[i][1] + f * (WAYPOINTS[i + 1][1] - WAYPOINTS[i][1]),
      ]
    }
    rem -= SEG_LENS[i]
  }
  return WAYPOINTS[WAYPOINTS.length - 1]
}

const RGB_IN  = [42,  72,  56]
const RGB_OUT = [10, 202, 176]

// ── State ─────────────────────────────────────────────────────────────────────

function makeRiverPart() {
  return {
    x: 1 + Math.random() * 6,
    y: 22 + Math.random() * 56,
  }
}

function initState() {
  return {
    riverParts: Array.from({ length: 14 }, makeRiverPart),
    flowParts: Array.from({ length: 18 }, (_, i) => ({
      t: (i / 18) * PATH_LEN,
    })),
    rawPumps: [
      { cx: 36, cy: 44, r: 3, angle: 0                  },
      { cx: 42, cy: 44, r: 3, angle: Math.PI / 2        },
      { cx: 36, cy: 56, r: 3, angle: Math.PI            },
      { cx: 42, cy: 56, r: 3, angle: 3 * Math.PI / 2    },
    ],
    treatedPumps: [
      { cx: 152, cy: 46, r: 2, angle: 0                  },
      { cx: 156, cy: 46, r: 2, angle: Math.PI / 2        },
      { cx: 152, cy: 54, r: 2, angle: Math.PI            },
      { cx: 156, cy: 54, r: 2, angle: 3 * Math.PI / 2    },
    ],
    chlorinePulse: 0,
    probePulse:    0,
    fish: [
      { x: 3, y: 30, dy:  0.05 },
      { x: 5, y: 65, dy: -0.065 },
    ],
  }
}

// ── Update ────────────────────────────────────────────────────────────────────

function update(s) {
  for (const p of s.riverParts) {
    p.y += 0.2
    if (p.y > 80) p.y -= 60
  }
  for (const p of s.flowParts) {
    p.t = (p.t + 0.3) % PATH_LEN
  }
  for (const arm of s.rawPumps)     arm.angle += 0.025
  for (const arm of s.treatedPumps) arm.angle += 0.035
  s.chlorinePulse = (s.chlorinePulse + 0.02) % (Math.PI * 2)
  s.probePulse    = (s.probePulse    + 0.03) % (Math.PI * 2)
  for (const f of s.fish) {
    f.y += f.dy
    if (f.y > 78 || f.y < 22) f.dy = -f.dy
  }
}

// ── Draw ──────────────────────────────────────────────────────────────────────

function draw(ctx, s) {
  // Ground
  px(ctx, 0, 0, VW, VH, C.GROUND)

  // ── American River (left edge, flowing south) ─────────────────────────────
  px(ctx, 0, 18, 8, 64, C.WATER_RAW)
  for (const p of s.riverParts) {
    px(ctx, p.x, p.y, 1, 2, '#3a6050')
  }

  // ── Intake / raw water collectors ─────────────────────────────────────────
  px(ctx, 8,  42, 10, 16, C.CONCRETE)
  px(ctx, 9,  44, 9,  12, C.WATER_RAW)
  // trash rack bars
  for (let i = 0; i < 4; i++) {
    px(ctx, 9 + i * 2, 44, 1, 12, '#5a6260')
  }

  // ── Raw water caisson (tall buried structure) ─────────────────────────────
  px(ctx, 18, 30, 14, 2,  C.WALL)            // cap
  px(ctx, 20, 32, 10, 38, C.CONCRETE)        // shaft
  px(ctx, 22, 36, 6,  30, C.WATER_1)         // water column
  // intake-to-caisson culvert
  px(ctx, 18, 49, 2, 2, C.PIPE)

  // ── Pipe from caisson to raw WPS ──────────────────────────────────────────
  px(ctx, 30, 49, 2, 2, C.PIPE)

  // ── Raw water pump station (4 pumps in 2x2) ───────────────────────────────
  px(ctx, 32, 36, 14, 2,  C.WALL)
  px(ctx, 32, 38, 14, 24, C.CONCRETE)
  px(ctx, 32, 62, 14, 2,  C.WALL)
  for (const p of s.rawPumps) {
    circ(ctx, p.cx, p.cy, p.r,     C.WALL)
    circ(ctx, p.cx, p.cy, p.r - 1, C.WATER_1)
    ctx.strokeStyle = '#8ab8a8'
    ctx.lineWidth   = 2
    ctx.beginPath()
    ctx.moveTo(p.cx * SCALE, p.cy * SCALE)
    ctx.lineTo(
      (p.cx + Math.cos(p.angle) * (p.r - 1)) * SCALE,
      (p.cy + Math.sin(p.angle) * (p.r - 1)) * SCALE,
    )
    ctx.stroke()
  }

  // ── Discharge header → splits to two CMF trains ───────────────────────────
  px(ctx, 46, 26, 2, 50, C.PIPE)             // vertical riser/dropper
  px(ctx, 46, 49, 2, 2,  C.PIPE)             // ties to pump station

  // ── CMF Train 1 (top, 8 units) ────────────────────────────────────────────
  px(ctx, 48, 20, 56, 2,  C.WALL)
  px(ctx, 48, 22, 56, 12, C.CONCRETE)
  px(ctx, 48, 34, 56, 2,  C.WALL)
  for (let i = 0; i < 8; i++) {
    const cx = 50 + i * 7
    px(ctx, cx,     24, 5, 8, C.MEMBRANE)
    px(ctx, cx + 1, 25, 1, 6, C.FIBRE)
    px(ctx, cx + 3, 25, 1, 6, C.FIBRE)
  }

  // ── CMF Train 2 (bottom, 8 units) ─────────────────────────────────────────
  px(ctx, 48, 64, 56, 2,  C.WALL)
  px(ctx, 48, 66, 56, 12, C.CONCRETE)
  px(ctx, 48, 78, 56, 2,  C.WALL)
  for (let i = 0; i < 8; i++) {
    const cx = 50 + i * 7
    px(ctx, cx,     68, 5, 8, C.MEMBRANE)
    px(ctx, cx + 1, 69, 1, 6, C.FIBRE)
    px(ctx, cx + 3, 69, 1, 6, C.FIBRE)
  }

  // ── Permeate header (both trains merge) ───────────────────────────────────
  px(ctx, 104, 26, 2, 50, C.PIPE)
  px(ctx, 102, 28, 4, 2,  C.PIPE)            // top branch
  px(ctx, 102, 74, 4, 2,  C.PIPE)            // bottom branch
  px(ctx, 106, 49, 6, 2,  C.PIPE)            // to CT tank

  // ── Chlorine contact tank (serpentine) ────────────────────────────────────
  px(ctx, 112, 36, 16, 2,  C.WALL)           // top
  px(ctx, 112, 38, 16, 24, C.WATER_2)        // body
  px(ctx, 112, 62, 16, 2,  C.WALL)           // bottom
  px(ctx, 112, 38, 2,  24, C.WALL)           // left
  px(ctx, 126, 38, 2,  24, C.WALL)           // right
  px(ctx, 117, 38, 1, 18, C.WALL)            // baffle 1
  px(ctx, 122, 44, 1, 18, C.WALL)            // baffle 2

  // chlorine injection — pulsing yellow dot above CT inlet
  const clBright = 0.6 + 0.4 * Math.sin(s.chlorinePulse)
  ctx.globalAlpha = clBright
  px(ctx, 113, 33, 2, 2, C.CHLORINE)
  ctx.globalAlpha = 1

  // ── 2 MG clear well ───────────────────────────────────────────────────────
  px(ctx, 130, 32, 18, 2,  C.WALL)
  px(ctx, 128, 32, 2,  40, C.WALL)
  px(ctx, 148, 32, 2,  40, C.WALL)
  px(ctx, 130, 70, 18, 2,  C.WALL)
  px(ctx, 130, 34, 18, 36, C.WATER_CLEAN)
  // surface shimmer
  for (let i = 0; i < 3; i++) {
    px(ctx, 132 + i * 5, 36, 3, 1, '#3acac0')
  }

  // ── Treated water pump station (4 pumps) ──────────────────────────────────
  px(ctx, 150, 40, 8, 2,  C.WALL)
  px(ctx, 150, 42, 8, 16, C.CONCRETE)
  px(ctx, 150, 58, 8, 2,  C.WALL)
  for (const p of s.treatedPumps) {
    circ(ctx, p.cx, p.cy, p.r,     C.WALL)
    circ(ctx, p.cx, p.cy, p.r - 1, C.WATER_CLEAN)
    ctx.strokeStyle = '#8ad8c8'
    ctx.lineWidth   = 1
    ctx.beginPath()
    ctx.moveTo(p.cx * SCALE, p.cy * SCALE)
    ctx.lineTo(
      (p.cx + Math.cos(p.angle) * (p.r - 1)) * SCALE,
      (p.cy + Math.sin(p.angle) * (p.r - 1)) * SCALE,
    )
    ctx.stroke()
  }

  // ── Distribution outlet + Cl₂ residual probe ─────────────────────────────
  px(ctx, 158, 49, 2, 2, C.WATER_OUT)
  // probe pulses
  const pBright = 0.6 + 0.4 * Math.sin(s.probePulse)
  ctx.globalAlpha = pBright
  px(ctx, 156, 45, 1, 1, C.PROBE)
  px(ctx, 156, 46, 1, 3, C.PROBE)
  ctx.globalAlpha = 1

  // ── Flow particles ────────────────────────────────────────────────────────
  for (const p of s.flowParts) {
    const [fx, fy] = posAtT(p.t)
    const frac = p.t / PATH_LEN
    const r = Math.round(RGB_IN[0] + frac * (RGB_OUT[0] - RGB_IN[0]))
    const g = Math.round(RGB_IN[1] + frac * (RGB_OUT[1] - RGB_IN[1]))
    const b = Math.round(RGB_IN[2] + frac * (RGB_OUT[2] - RGB_IN[2]))
    px(ctx, Math.floor(fx), Math.floor(fy), 2, 2, `rgb(${r},${g},${b})`)
  }

  // ── Labels ─────────────────────────────────────────────────────────────────
  ctx.font      = '11px monospace'
  ctx.fillStyle = '#5a8870'
  ctx.textAlign = 'center'
  ctx.fillText('AMERICAN R.',     4   * SCALE, 14 * SCALE)
  ctx.fillText('INTAKE',          13  * SCALE, 38 * SCALE)
  ctx.fillText('CAISSON',         25  * SCALE, 26 * SCALE)
  ctx.fillText('RAW WPS',         39  * SCALE, 32 * SCALE)
  ctx.fillText('CMF TRAIN 1',     76  * SCALE, 16 * SCALE)
  ctx.fillText('CMF TRAIN 2',     76  * SCALE, 90 * SCALE)
  ctx.fillText('CT TANK',         120 * SCALE, 32 * SCALE)
  ctx.fillText('2 MG CLEAR WELL', 139 * SCALE, 28 * SCALE)
  ctx.fillText('TREATED WPS',     154 * SCALE, 36 * SCALE)
  ctx.textAlign = 'right'
  ctx.fillText('DISTRIBUTION',    159 * SCALE, 60 * SCALE)
  ctx.fillStyle = C.PROBE
  ctx.fillText('Cl₂ RESIDUAL',    159 * SCALE, 70 * SCALE)
  ctx.textAlign = 'left'

  // ── Fish in the river (always good to keep some life) ─────────────────────
  for (const f of s.fish) {
    const fx = Math.floor(f.x)
    const fy = Math.floor(f.y)
    px(ctx, fx, fy, 2, 2, '#4ae0d0')
    const ty = f.dy > 0 ? fy - 1 : fy + 2
    px(ctx, fx + 1, ty, 1, 1, '#3ab8b0')
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function WaterTreatmentArt() {
  const canvasRef = useRef(null)
  const stateRef  = useRef(null)
  const rafRef    = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx    = canvas.getContext('2d')
    ctx.imageSmoothingEnabled = false
    stateRef.current = initState()

    function loop() {
      update(stateRef.current)
      draw(ctx, stateRef.current)
      rafRef.current = requestAnimationFrame(loop)
    }
    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return (
    <canvas
      ref={canvasRef}
      width={VW * SCALE}
      height={VH * SCALE}
      style={{ display: 'block', width: '100%', height: 'auto', imageRendering: 'pixelated' }}
    />
  )
}
