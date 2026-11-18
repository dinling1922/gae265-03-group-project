import type { ReactNode } from "react";

// 設計稿（UI v1.8）的 inline SVG icon；顏色跟著 currentColor，大小由使用處用 className 決定。

type IconProps = { className?: string };

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

export function PlaneIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
      <path
        d="M21.5 15.2v-1.9l-8.3-5.2V3.3c0-.8-.6-1.5-1.3-1.5s-1.3.7-1.3 1.5v4.8l-8.3 5.2v1.9l8.3-2.6v4.8l-2.2 1.6v1.5l3.5-1 3.5 1v-1.5l-2.2-1.6v-4.8z"
        transform="rotate(45 12 12)"
      />
    </svg>
  );
}

export function ShieldIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M12 2.8 4.5 5.6v6c0 4.6 3.1 8.2 7.5 9.6 4.4-1.4 7.5-5 7.5-9.6v-6z" />
      <path d="m8.6 12 2.4 2.4 4.6-4.8" />
    </svg>
  );
}

export function DocIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M14 2.8H6.5A1.5 1.5 0 0 0 5 4.3v15.4a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5V7.8z" />
      <path d="M14 2.8v5h5M8.5 12.5h7M8.5 16.5h7" />
    </svg>
  );
}

export function ArrowIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2.4" aria-hidden="true" className={className}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function ChevronIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2.6" aria-hidden="true" className={className}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

export function UserIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true" className={className}>
      <circle cx="20" cy="20" r="18" fill="none" stroke="currentColor" strokeWidth="3" />
      <circle cx="20" cy="15.5" r="6" fill="currentColor" />
      <path
        d="M8.5 31c2.4-5 6.6-7.2 11.5-7.2S29.1 26 31.5 31A16 16 0 0 1 8.5 31z"
        fill="currentColor"
      />
    </svg>
  );
}

// 功能一 1-2 的 9 個在意事項圖示（線條，stroke 2，跟著 currentColor）
function Line({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      {children}
    </svg>
  );
}

/** 突然生病就醫 */
export function MedIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="3.5" y="3.5" width="17" height="17" rx="4" />
      <path d="M12 8v8M8 12h8" />
    </Line>
  );
}

/** 需要緊急送醫或家屬趕來 */
export function PulseIcon(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M2.5 12.5h4l2.5-6 4 11 2.5-5h6" />
    </Line>
  );
}

/** 行程取消或更改 */
export function XCalIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" />
      <path d="M3.5 10h17M8 3v4M16 3v4M10 13.5l4 4M14 13.5l-4 4" />
    </Line>
  );
}

/** 班機延誤 */
export function ClockIcon(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.2 2" />
    </Line>
  );
}

/** 行李延誤 */
export function BagIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="4.5" y="7.5" width="15" height="13" rx="2.5" />
      <path d="M9 7.5V5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5v2.5M9 11.5v5M15 11.5v5" />
    </Line>
  );
}

/** 行李遺失或損壞 */
export function BagWarnIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="4.5" y="7.5" width="15" height="13" rx="2.5" />
      <path d="M9 7.5V5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5v2.5M12 11.5v3.2M12 17.2v.1" />
    </Line>
  );
}

/** 證件遺失 */
export function IdIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <circle cx="8.5" cy="11" r="2" />
      <path d="M5.8 16c.6-1.6 1.6-2.3 2.7-2.3s2.1.7 2.7 2.3M14 10h4.5M14 13.5h3" />
    </Line>
  );
}

/** 信用卡盜刷 */
export function CardIcon(props: IconProps) {
  return (
    <Line {...props}>
      <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
      <path d="M3 10h18M7 15h3.5" />
    </Line>
  );
}

/** 不小心弄傷別人或弄壞東西 */
export function WarnIcon(props: IconProps) {
  return (
    <Line {...props}>
      <path d="M12 3.5 21.5 19.5h-19z" />
      <path d="M12 10v4.2M12 16.8v.1" />
    </Line>
  );
}

/** 名詞按鈕後面的 ⓘ */
export function InfoIcon(props: IconProps) {
  return (
    <Line {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.2M12 7.8v.1" />
    </Line>
  );
}

// 日程活動的分類圖示（線條、stroke 2、圓角端點，風格同 DESIGN.md 5.3）

export function EatIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M3 2v7a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V2M7 2v20M21 15V2a5 5 0 0 0-5 5v6a2 2 0 0 0 2 2h3Zm0 0v7" />
    </svg>
  );
}

export function CompassIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <circle cx="12" cy="12" r="10" />
      <path d="m16.2 7.8-1.8 5.4a2 2 0 0 1-1.2 1.2l-5.4 1.8 1.8-5.4a2 2 0 0 1 1.2-1.2z" />
    </svg>
  );
}

export function LandmarkIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M3 22h18M6 18v-7M10 18v-7M14 18v-7M18 18v-7M12 2l8 5H4z" />
    </svg>
  );
}

export function CoffeeIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M10 2v2M14 2v2M6 2v2M16 8h1a4 4 0 1 1 0 8h-1M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z" />
    </svg>
  );
}

export function MoonIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
  );
}

export function BedIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M2 4v16M2 8h18a2 2 0 0 1 2 2v10M2 17h20M6 8v9" />
    </svg>
  );
}

export function PinIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  );
}

export function PencilIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

export function CalendarIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <rect x="3" y="4.5" width="18" height="16.5" rx="2" />
      <path d="M3 10h18M8 2.5v4M16 2.5v4" />
    </svg>
  );
}

export function SparkleIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" />
    </svg>
  );
}

/** 信件（尚未寄出）：提醒信寄送進度用；Lucide 的 mail，stroke 2。 */
export function MailIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
    </svg>
  );
}

/** 信件已寄出（信封＋勾）：提醒信寄送進度用；Lucide 的 mail-check，stroke 2。 */
export function MailSentIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M22 13V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v12c0 1.1.9 2 2 2h8" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
      <path d="m16 19 2 2 4-4" />
    </svg>
  );
}

/** 信件不再寄（信封＋叉）：提醒信寄送進度用；Lucide 的 mail-x，stroke 2。 */
export function MailOffIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} strokeWidth="2" aria-hidden="true" className={className}>
      <path d="M22 13V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v12c0 1.1.9 2 2 2h9" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
      <path d="m17 17 4 4M21 17l-4 4" />
    </svg>
  );
}
