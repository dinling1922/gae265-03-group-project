import { DialogShell } from "../../components/ui/DialogShell";

type Kind = "send" | "draft";

const TEXT: Record<Kind, { title: string; body: string }> = {
  send: {
    title: "信件寄出中",
    body: "正在將信件寄給保險公司，附件較多時會需要一點時間，請不要關閉頁面。",
  },
  draft: {
    title: "建立草稿中",
    body: "正在將信件內容建立於草稿，附件較多時會需要一點時間，請不要關閉頁面。",
  },
};

/** 寄出：信封往右飛出去，後面拖著速度線。 */
function Sending() {
  return (
    <svg viewBox="0 0 160 72" className="h-18 w-40" aria-hidden="true">
      <g className="send-speed" stroke="var(--color-leaf)" strokeWidth="3" strokeLinecap="round">
        <path d="M14 26h22" />
        <path d="M6 36h30" />
        <path d="M18 46h18" />
      </g>
      <g className="send-fly">
        <rect
          x="48"
          y="16"
          width="56"
          height="40"
          rx="5"
          fill="#fff"
          stroke="var(--color-brand)"
          strokeWidth="3"
        />
        <path
          d="m50 20 26 19 26-19"
          fill="none"
          stroke="var(--color-brand)"
          strokeWidth="3"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}

/** 建立草稿：鉛筆在信紙上一行一行寫。 */
function Drafting() {
  return (
    <svg viewBox="0 0 160 72" className="h-18 w-40" aria-hidden="true">
      <rect
        x="36"
        y="6"
        width="88"
        height="60"
        rx="5"
        fill="#fff"
        stroke="var(--color-brand)"
        strokeWidth="3"
      />
      <g stroke="var(--color-leaf)" strokeWidth="3" strokeLinecap="round">
        <path className="draft-line draft-line-1" d="M48 22h64" />
        <path className="draft-line draft-line-2" d="M48 36h64" />
        <path className="draft-line draft-line-3" d="M48 50h40" />
      </g>
      <g className="draft-pencil">
        <path
          d="M0 0 6 -2 30 -26 26 -30 2 -6Z"
          fill="var(--color-sun)"
          stroke="var(--color-brand)"
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}

/**
 * 按下寄出、或「我自己處理就好」選了建立草稿之後，等後端寄完／建好前顯示的視窗。
 * 寄出或建草稿要從 Drive 取附件、再交給 Gmail，可能要等幾秒；完成後換成「已寄出」「已存成 Gmail 草稿」的視窗。
 * 等待中不能關掉（點遮罩、按 Esc 都不關），避免使用者以為沒反應又按一次。
 */
export function SubmitProgressDialog({ kind }: { kind: Kind | null }) {
  const text = kind ? TEXT[kind] : null;
  return (
    <DialogShell
      open={kind !== null}
      onOpenChange={() => {}}
      title={text?.title ?? ""}
      testId="submit-progress-dialog"
      description={text && <p>{text.body}</p>}
    >
      {kind && (
        <div
          className="flex justify-center py-2"
          role="status"
          aria-label={text?.title}
          data-testid={`submit-progress-${kind}`}
        >
          {kind === "send" ? <Sending /> : <Drafting />}
        </div>
      )}
    </DialogShell>
  );
}
