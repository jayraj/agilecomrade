import { useEffect, useState } from 'react'
import { getRiskColor } from '../../utils/severity'

const GAUGE_STROKE = 9

interface SprintGaugeProps {
  score?: number | null
  size?: number
}

export default function SprintGauge({ score, size = 65 }: SprintGaugeProps) {
  const r = (size - GAUGE_STROKE) / 2
  const circumference = 2 * Math.PI * r
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true))
    return () => cancelAnimationFrame(id)
  }, [])
  const value = Math.max(0, Math.min(100, score ?? 0))
  const dashOffset = shown ? circumference * (1 - value / 100) : circumference
  const color = getRiskColor(score)
  const trackColor = `${color}33`

  return (
    <div
      className="sprint-card-gauge"
      style={{ width: size, height: size, flex: `0 0 ${size}px`, boxShadow: `0 0 18px 1px ${color}26` }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={trackColor}
          strokeWidth={GAUGE_STROKE}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={GAUGE_STROKE}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className="sprint-card-gauge-arc"
          style={{ transition: 'stroke 0.3s ease' }}
        />
      </svg>
      <div className="sprint-card-gauge-center">
        <span className="sprint-card-gauge-pct">{value}%</span>
        <span className="sprint-card-gauge-sub">risk</span>
      </div>
    </div>
  )
}
