import type { ReactNode, RefObject } from "react";
import { AskBar, ChatLog, ChatMessage, TypingBubble } from "../../components/ui/Chat";
import { Chip, ChipGroup } from "../../components/ui/Chip";
import { box } from "./shared";

/** 2-2 的 AI 第一句（畫面上的開場白，不是 5 號的回覆，不存進對話紀錄）。 */
export const FIRST_QUESTION =
  "發生什麼事了？用一兩句話描述就好，例如：颱風來了，回台灣的班機延誤了。";

export type ChatLine = {
  from: "me" | "ai";
  text: string;
  citations?: { insurance_code: string; article_no: number }[];
};

/**
 * 2-2 描述事故的對話卡（DESIGN.md 7.9）：對話區（最小高 320）、AI 追問的選項 Chip、提問列（500 字）。
 * 建立前（還沒有事件）與追問中共用。送出中時輸入框與選項停用（輸入框保留內容），對話區最下面顯示
 * AI「輸入中」的泡泡（建立事件那頁與追問頁一樣，不蓋全頁遮罩）。
 */
export function DescribeChat({
  lines,
  quickReplies,
  placeholder,
  value,
  onChange,
  busy,
  onSend,
  maxLength = 500,
  minHeight = 320,
  tall = false,
  fill = false,
  logFooter,
  typing,
  typingLabel,
  askLeading,
  focusInput = false,
  inputRef,
}: {
  lines: ChatLine[];
  quickReplies: string[];
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  busy: boolean;
  onSend: (text: string, mode: "text" | "quick_reply") => void;
  /** 字數上限；描述事故 500，理賠頁的問答 300。 */
  maxLength?: number;
  /** 對話區最小高（px）。 */
  minHeight?: number;
  /** 桌機（lg 以上）固定高度、內部捲動，輸入列貼底（雙欄版型用）。 */
  tall?: boolean;
  /** 手機（lg 以下）撐滿外層：聊天區吃掉剩下的高度，輸入列貼底（理賠頁的問答抽屜用）。 */
  fill?: boolean;
  /** 放在對話區最下方左側的東西（理賠頁的「＋ 有新狀況」）。 */
  logFooter?: ReactNode;
  /** 是否顯示 AI「輸入中」泡泡；預設在送出中（`busy`）時顯示。 */
  typing?: boolean;
  /** 輸入中泡泡旁邊寫正在做的事（例如「正在確認狀況」）；沒給就只有三個點。 */
  typingLabel?: string;
  /** 放在提問列字數與送出鈕之間的東西（矮視窗時把底部動作列的按鈕搬上來）。 */
  askLeading?: ReactNode;
  /** 掛載時自動對焦輸入框（只有桌機；從建立事故頁送出描述後進到追問頁用，#441）。 */
  focusInput?: boolean;
  /** 讓外面拿得到提問列的輸入框。 */
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const showTyping = typing ?? busy;
  return (
    <section
      className={fill ? `${box} max-lg:flex max-lg:min-h-0 max-lg:flex-1 max-lg:flex-col` : box}
      aria-label="對話"
    >
      <ChatLog
        minHeight={minHeight}
        className={`${tall ? "lg:h-[calc(100vh-22rem)] lg:max-h-none lg:min-h-[420px]" : ""} ${
          fill ? "max-lg:max-h-none max-lg:min-h-0 max-lg:flex-1" : ""
        }`}
        data-testid="messages"
      >
        {lines.map((line, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 訊息沒有穩定 id，順序固定
          <ChatMessage key={i} from={line.from}>
            {line.text}
            {line.citations && line.citations.length > 0 && (
              <span className="t-caption text-muted" data-testid="citations">
                依據：
                {line.citations.map((c) => `${c.insurance_code} 第 ${c.article_no} 條`).join("、")}
              </span>
            )}
          </ChatMessage>
        ))}
        {showTyping && <TypingBubble label={typingLabel} />}
        {logFooter && (
          <li className="mt-auto list-none pt-2" data-testid="log-footer">
            {logFooter}
          </li>
        )}
      </ChatLog>
      {quickReplies.length > 0 && (
        <ChipGroup data-testid="quick-replies">
          {quickReplies.map((q) => (
            <Chip key={q} disabled={busy} onClick={() => onSend(q, "quick_reply")}>
              {q}
            </Chip>
          ))}
        </ChipGroup>
      )}
      <AskBar
        value={value}
        onChange={onChange}
        onSubmit={() => onSend(value, "text")}
        maxLength={maxLength}
        placeholder={placeholder}
        disabled={busy}
        inputName="content"
        inputTestId="message"
        submitTestId="send"
        leading={askLeading}
        focusOnMount={focusInput}
        inputRef={inputRef}
      />
    </section>
  );
}
