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
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-16">
      <div className="text-5xl mb-4">📈</div>
      <h1 className="text-3xl font-semibold text-text tracking-tight mb-2">
        Hôm nay bạn muốn phân tích gì?
      </h1>
      <p className="text-muted text-base mb-10 text-center max-w-md">
        Hỏi về giá cổ phiếu, phân tích kỹ thuật, tài chính doanh nghiệp, tin tức thị trường
      </p>

      <div className="grid grid-cols-2 gap-3 w-full max-w-xl">
        {SUGGESTIONS.map(({ icon, text }) => (
          <button
            key={text}
            onClick={() => onSuggest(text)}
            className="flex flex-col gap-1.5 bg-surface hover:bg-[#363636] border border-border rounded-xl px-4 py-4 text-left transition group"
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
