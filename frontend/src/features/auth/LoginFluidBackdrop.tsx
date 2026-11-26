import { useId } from 'react'

/**
 * Pure-SVG enterprise fluid backdrop for the login brand panel.
 * Renders soft ambient radial glows, 4 sweeping Bezier ribbon waves,
 * a masked fine dot grid, and a subtle central contrast scrim.
 */
export function LoginFluidBackdrop() {
  const uid = useId().replace(/:/g, '')
  const gridPatternId = `okteam-dot-grid-${uid}`
  const ribbonFillAId = `okteam-ribbon-fill-a-${uid}`
  const ribbonFillBId = `okteam-ribbon-fill-b-${uid}`
  const ribbonStrokeAId = `okteam-ribbon-stroke-a-${uid}`
  const ribbonStrokeBId = `okteam-ribbon-stroke-b-${uid}`
  const ribbonStrokeCId = `okteam-ribbon-stroke-c-${uid}`
  const ribbonStrokeDId = `okteam-ribbon-stroke-d-${uid}`

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-hidden select-none"
    >
      {/* Layer 1: Soft Ambient Radial Glows (12%-18% opacity) */}
      <div
        className="absolute -bottom-24 -left-24 h-[520px] w-[520px] rounded-full"
        style={{
          background:
            'radial-gradient(circle, rgba(37, 99, 235, 0.16) 0%, rgba(37, 99, 235, 0.06) 44%, transparent 72%)',
        }}
      />
      <div
        className="absolute -top-20 -right-20 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            'radial-gradient(circle, rgba(15, 118, 110, 0.14) 0%, rgba(15, 118, 110, 0.05) 45%, transparent 74%)',
        }}
      />

      {/* Layer 2: Pure SVG Fluid Waves & Curvature Ribbons */}
      <svg
        viewBox="0 0 800 1000"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 h-full w-full"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient
            id={ribbonFillAId}
            x1="0%"
            y1="100%"
            x2="100%"
            y2="0%"
          >
            <stop offset="0%" stopColor="#2563EB" stopOpacity="0.16" />
            <stop offset="55%" stopColor="#38BDF8" stopOpacity="0.08" />
            <stop offset="100%" stopColor="#0F766E" stopOpacity="0.04" />
          </linearGradient>

          <linearGradient
            id={ribbonFillBId}
            x1="0%"
            y1="100%"
            x2="100%"
            y2="10%"
          >
            <stop offset="0%" stopColor="#0F766E" stopOpacity="0.12" />
            <stop offset="50%" stopColor="#2563EB" stopOpacity="0.09" />
            <stop offset="100%" stopColor="#38BDF8" stopOpacity="0.03" />
          </linearGradient>

          <linearGradient
            id={ribbonStrokeAId}
            x1="0%"
            y1="100%"
            x2="100%"
            y2="0%"
          >
            <stop offset="0%" stopColor="#2563EB" stopOpacity="0.22" />
            <stop offset="50%" stopColor="#38BDF8" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#0F766E" stopOpacity="0.08" />
          </linearGradient>

          <linearGradient
            id={ribbonStrokeBId}
            x1="5%"
            y1="95%"
            x2="95%"
            y2="5%"
          >
            <stop offset="0%" stopColor="#38BDF8" stopOpacity="0.14" />
            <stop offset="55%" stopColor="#2563EB" stopOpacity="0.20" />
            <stop offset="100%" stopColor="#38BDF8" stopOpacity="0.06" />
          </linearGradient>

          <linearGradient
            id={ribbonStrokeCId}
            x1="0%"
            y1="100%"
            x2="100%"
            y2="0%"
          >
            <stop offset="0%" stopColor="#0F766E" stopOpacity="0.18" />
            <stop offset="55%" stopColor="#38BDF8" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#2563EB" stopOpacity="0.08" />
          </linearGradient>

          <linearGradient
            id={ribbonStrokeDId}
            x1="0%"
            y1="100%"
            x2="100%"
            y2="0%"
          >
            <stop offset="0%" stopColor="#2563EB" stopOpacity="0.10" />
            <stop offset="50%" stopColor="#0F766E" stopOpacity="0.16" />
            <stop offset="100%" stopColor="#38BDF8" stopOpacity="0.12" />
          </linearGradient>
        </defs>

        {/* Drift Group A (20s cycle): Sweeping primary ribbons */}
        <g className="okteam-fluid-drift-a">
          {/* Ribbon 1: Broad lower-left to upper-right translucent wave */}
          <path
            d="M -120 960 C 90 830, 190 610, 410 510 C 610 420, 710 220, 920 40 L 920 210 C 730 370, 620 550, 420 635 C 210 725, 110 905, -120 1050 Z"
            fill={`url(#${ribbonFillAId})`}
          />
          <path
            d="M -120 960 C 90 830, 190 610, 410 510 C 610 420, 710 220, 920 40"
            fill="none"
            stroke={`url(#${ribbonStrokeAId})`}
            strokeWidth="1.75"
          />

          {/* Ribbon 2: Secondary upper contour curve */}
          <path
            d="M -80 820 C 120 710, 230 490, 455 390 C 640 308, 725 140, 880 -40"
            fill="none"
            stroke={`url(#${ribbonStrokeBId})`}
            strokeWidth="1.25"
          />
        </g>

        {/* Drift Group B (24s cycle): Counter-drifting teal & cobalt contours */}
        <g className="okteam-fluid-drift-b">
          {/* Ribbon 3: Lower-sweep translucent ribbon */}
          <path
            d="M -90 1040 C 160 910, 270 740, 495 650 C 675 578, 760 405, 930 230 L 930 375 C 760 520, 665 675, 485 745 C 275 828, 175 975, -90 1110 Z"
            fill={`url(#${ribbonFillBId})`}
          />
          <path
            d="M -90 1040 C 160 910, 270 740, 495 650 C 675 578, 760 405, 930 230"
            fill="none"
            stroke={`url(#${ribbonStrokeCId})`}
            strokeWidth="1.5"
          />

          {/* Ribbon 4: Fine harmonic curvature line */}
          <path
            d="M -60 720 C 150 640, 290 560, 470 465 C 655 368, 745 260, 900 130"
            fill="none"
            stroke={`url(#${ribbonStrokeDId})`}
            strokeWidth="1"
          />
        </g>
      </svg>

      {/* Layer 3: Fine Dot Grid (<= 6% opacity) with Radial Vignette Mask */}
      <div
        className="absolute inset-0 opacity-[0.055]"
        style={{
          maskImage:
            'radial-gradient(ellipse 78% 72% at 48% 50%, #000 35%, transparent 100%)',
          WebkitMaskImage:
            'radial-gradient(ellipse 78% 72% at 48% 50%, #000 35%, transparent 100%)',
        }}
      >
        <svg
          className="h-full w-full"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <pattern
              id={gridPatternId}
              width="24"
              height="24"
              patternUnits="userSpaceOnUse"
            >
              <circle cx="1.5" cy="1.5" r="1" fill="#E2E8F0" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill={`url(#${gridPatternId})`} />
        </svg>
      </div>

      {/* Subtle central dark scrim to guarantee crisp WCAG AA contrast for z-10 text */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 74% 48% at 38% 50%, rgba(11, 17, 32, 0.45) 0%, rgba(11, 17, 32, 0.16) 62%, transparent 100%)',
        }}
      />
    </div>
  )
}
