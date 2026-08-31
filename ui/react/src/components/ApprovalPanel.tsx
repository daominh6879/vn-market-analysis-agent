import React, { useEffect, useState } from 'react'
import type { PendingSession } from '../types'
import { getPendingSessions, approveSession, rejectSession, fmtDate } from '../api'

const VERDICT_COLORS: Record<string, string> = {
  buy: 'text-green-400 bg-green-400/10',
  sell: 'text-red-400 bg-red-400/10',
  hold: 'text-yellow-400 bg-yellow-400/10',
  neutral: 'text-blue-400 bg-blue-400/10',
}

interface ApproveResult {
  session_id: string
  ticker: string
  risk_verdict: string
  report: string
}

export default function ApprovalPanel() {
  const [sessions, setSessions] = useState<PendingSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [result, setResult] = useState<ApproveResult | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setSessions(await getPendingSessions())
    } catch {
      setError('Không tải được danh sách.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleApprove(session_id: string) {
    setBusy(b => ({ ...b, [session_id]: true }))
    try {
      const res = await approveSession(session_id)
      setResult(res)
      setSessions(s => s.filter(x => x.session_id !== session_id))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(b => ({ ...b, [session_id]: false }))
    }
  }

  async function handleReject(session_id: string) {
    setBusy(b => ({ ...b, [session_id]: true }))
    try {
      await rejectSession(session_id)
      setSessions(s => s.filter(x => x.session_id !== session_id))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(b => ({ ...b, [session_id]: false }))
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-text">Phê duyệt phiên</h2>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-sm text-text-muted hover:text-text hover:bg-surface transition disabled:opacity-50"
        >
          <svg className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Làm mới
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 rounded-lg bg-red-400/10 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Approve result */}
      {result && (
        <div className="mb-4 rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold text-text">{result.ticker} — Đã duyệt</span>
            <button onClick={() => setResult(null)} className="text-muted hover:text-text text-lg leading-none">&times;</button>
          </div>
          <p className="text-xs text-muted mb-2">risk_verdict: <span className="text-text">{result.risk_verdict}</span></p>
          <pre className="text-xs text-text-muted whitespace-pre-wrap max-h-48 overflow-y-auto">{result.report}</pre>
        </div>
      )}

      {/* Sessions table */}
      {!loading && sessions.length === 0 && !result && (
        <p className="text-sm text-muted mt-8 text-center">Không có phiên nào đang chờ duyệt.</p>
      )}

      {sessions.length > 0 && (
        <div className="overflow-y-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface text-muted text-xs uppercase tracking-wider">
                <th className="px-4 py-2 text-left">Ticker</th>
                <th className="px-4 py-2 text-left">Verdict</th>
                <th className="px-4 py-2 text-left">Tạo lúc</th>
                <th className="px-4 py-2 text-left">Hết hạn</th>
                <th className="px-4 py-2 text-right">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => {
                const isBusy = !!busy[s.session_id]
                const vColor = VERDICT_COLORS[s.risk_verdict?.toLowerCase() ?? ''] ?? 'text-muted bg-surface'
                return (
                  <tr key={s.session_id} className="border-b border-border last:border-0 hover:bg-surface/50 transition">
                    <td className="px-4 py-3 font-mono font-semibold text-text">{s.ticker}</td>
                    <td className="px-4 py-3">
                      {s.risk_verdict
                        ? <span className={`px-2 py-0.5 rounded text-xs font-medium ${vColor}`}>{s.risk_verdict}</span>
                        : <span className="text-muted">—</span>
                      }
                    </td>
                    <td className="px-4 py-3 text-muted">{fmtDate(s.created_at)}</td>
                    <td className="px-4 py-3 text-muted">{fmtDate(s.expires_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => handleApprove(s.session_id)}
                          disabled={isBusy}
                          className="px-3 py-1 rounded-lg bg-green-500/15 text-green-400 hover:bg-green-500/25 text-xs font-medium transition disabled:opacity-40"
                        >
                          {isBusy ? '...' : 'Duyệt'}
                        </button>
                        <button
                          onClick={() => handleReject(s.session_id)}
                          disabled={isBusy}
                          className="px-3 py-1 rounded-lg bg-red-500/15 text-red-400 hover:bg-red-500/25 text-xs font-medium transition disabled:opacity-40"
                        >
                          {isBusy ? '...' : 'Từ chối'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
