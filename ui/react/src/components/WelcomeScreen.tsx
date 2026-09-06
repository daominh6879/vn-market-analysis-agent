import React from 'react'

const SUGGESTIONS = [
  { icon: '📊', text: 'HPG giá hôm nay?' },
  { icon: '🏦', text: 'VCB P/E ngành ngân hàng?' },
  { icon: '🌏', text: 'Thị trường hôm nay?' },
  { icon: '🔍', text: 'Lọc RSI dưới 30 ngành thép?' },
]

interface Props {
  onSuggest: (text: string) => void
}

export default function WelcomeScreen({ onSuggest }: Props) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-16 overflow-y-auto">
      <div className="text-5xl mb-4 drop-shadow-[0_0_18px_rgba(16,163,127,0.35)]">📈</div>
      <h1 className="text-3xl sm:text-4xl font-semibold text-text tracking-tight mb-2 text-center">
        Hôm nay bạn muốn{' '}
        <span className="bg-gradient-to-r from-accent to-emerald-400 bg-clip-text text-transparent">
          phân tích
        </span>{' '}
        gì?
      </h1>
      <p className="text-muted text-base mb-10 text-center max-w-md">
        Hỏi về giá cổ phiếu, phân tích kỹ thuật, tài chính doanh nghiệp, tin tức thị trường
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl">
        {SUGGESTIONS.map(({ icon, text }) => (
          <button
            key={text}
            onClick={() => onSuggest(text)}
            className="flex flex-col gap-1.5 bg-surface hover:bg-[#363636] hover:border-accent/50 border border-border rounded-xl px-4 py-4 text-left transition group"
          >
            <span className="text-xl">{icon}</span>
            <span className="text-sm text-text-muted group-hover:text-text transition leading-5">
              {text}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
