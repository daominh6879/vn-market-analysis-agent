import React, { useEffect, useState } from 'react'
import { getDashboardStats } from '../api'
import type { DashboardStats } from '../types'

// Bài 26 monitoring dashboard: tool status/error-rate/latency + LLM cost.

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${v.toFixed(4)}`
}

function fmtMs(v: number | null | undefined): string {
  if (v == null) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`
}

export default function DashboardView() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDashboardStats()
      .then(s => setStats(s))
      .catch(e => setError(String(e)))
  }, [])

  if (error) return <div className="p-4 text-sm text-red-400">Lỗi: {error}</div>
  if (!stats) return <div className="p-4 text-sm text-muted">Đang tải…</div>

  const s = stats.settings ?? {}

  return (
    <div className="p-4 space-y-5">
      {/* summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['Lượt truy vấn', s.total_turns ?? 0, ''],
          ['Lời gọi tool', s.total_tool_calls ?? 0, ''],
          ['Chi phí LLM', fmtUsd(s.total_cost_usd as number), ''],
          ['Provider', s.provider ?? '—', `${s.model ?? ''}`],
        ].map(([label, value, sub], i) => (
          <div key={i} className="bg-surface border border-border rounded-xl p-4">
            <p className="text-[11px] uppercase tracking-wider text-muted">{label}</p>
            <p className="text-xl font-semibold text-text mt-1">{value}</p>
            {sub && <p className="text-[11px] text-muted mt-0.5 truncate">{sub}</p>}
          </div>
        ))}
      </div>

      {/* tool status table */}
      <div className="bg-surface border border-border rounded-xl overflow-hidden">
        <h3 className="px-4 py-3 text-sm font-semibold text-text border-b border-border">
          Tool calls — status & latency
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted text-[11px] uppercase tracking-wider border-b border-border">
                <th className="px-4 py-2 font-medium">Tool</th>
                <th className="px-4 py-2 font-medium text-right">Calls</th>
                <th className="px-4 py-2 font-medium text-right">Errors</th>
                <th className="px-4 py-2 font-medium text-right">Error %</th>
                <th className="px-4 py-2 font-medium text-right">Avg latency</th>
              </tr>
            </thead>
            <tbody>
              {stats.tools.map(t => (
                <tr key={t.name} className="border-b border-border last:border-0 hover:bg-[#2a2a2a]">
                  <td className="px-4 py-2 text-text font-mono text-xs">{t.name}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{t.calls}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{t.errors}</td>
                  <td className={`px-4 py-2 text-right ${t.error_rate > 0 ? 'text-red-400' : 'text-text-muted'}`}>
                    {t.error_rate}%
                  </td>
                  <td className="px-4 py-2 text-right text-text-muted">{fmtMs(t.avg_ms)}</td>
                </tr>
              ))}
              {stats.tools.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-muted">
                    Chưa có tool call nào — chạy một lượt phân tích trước.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* LLM usage by model */}
      <div className="bg-surface border border-border rounded-xl overflow-hidden">
        <h3 className="px-4 py-3 text-sm font-semibold text-text border-b border-border">
          LLM usage — tokens & cost per model
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted text-[11px] uppercase tracking-wider border-b border-border">
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 font-medium text-right">Calls</th>
                <th className="px-4 py-2 font-medium text-right">In</th>
                <th className="px-4 py-2 font-medium text-right">Out</th>
                <th className="px-4 py-2 font-medium text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {stats.usage_summary.map(u => (
                <tr key={u.model} className="border-b border-border last:border-0 hover:bg-[#2a2a2a]">
                  <td className="px-4 py-2 text-text font-mono text-xs">{u.model}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{u.calls}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{u.input_tokens.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{u.output_tokens.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{fmtUsd(u.cost_usd)}</td>
                </tr>
              ))}
              {stats.usage_summary.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-muted">
                    Chưa có dữ liệu usage.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
