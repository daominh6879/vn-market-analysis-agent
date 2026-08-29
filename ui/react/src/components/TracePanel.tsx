import React from 'react'
import type { TraceStep } from '../types'
import { AGENT_LABELS, STEP_LABELS } from '../api'

interface Props {
  steps: TraceStep[]
  streaming: boolean
}

export default function TracePanel({ steps, streaming }: Props) {
  if (steps.length === 0 && !streaming) return null

  return (
    <div className="mt-3 border border-border rounded-xl overflow-hidden text-xs">
      <div className="bg-[#1a1a1a] px-3 py-2 border-b border-border text-[11px] text-muted font-semibold uppercase tracking-wider flex items-center gap-2">
        <span>🔍 Execution Trace</span>
        {streaming && (
          <span className="flex gap-0.5 ml-1">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:300ms]" />
          </span>
        )}
      </div>

      <div className="divide-y divide-border bg-[#1a1a1a]">
        {steps.map((step, i) => {
          const agentLabel = AGENT_LABELS[step.agent] ?? step.agent
          const stepLabel = STEP_LABELS[step.step] ?? step.step
          return (
            <div key={i} className="flex items-center gap-3 px-3 py-2.5">
              <div className="flex-shrink-0 w-5 h-5 flex items-center justify-center">
                {step.done ? (
                  <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <span className="w-2 h-2 rounded-full bg-accent/60 animate-pulse" />
                )}
              </div>
              <div className="flex-1 min-w-0 text-text-muted">
                {step.agent && <span className="mr-1.5">{agentLabel}</span>}
                <span className="text-muted">{stepLabel}</span>
              </div>
              <span className="flex-shrink-0 text-[10px] text-muted/50 font-mono">#{i + 1}</span>
            </div>
          )
        })}

        {streaming && steps.length === 0 && (
          <div className="flex items-center gap-3 px-3 py-2.5">
            <span className="w-2 h-2 rounded-full bg-accent/60 animate-pulse" />
            <span className="text-muted">Đang xử lý...</span>
          </div>
        )}
      </div>
    </div>
  )
}
