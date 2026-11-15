import { type ReactNode, useState } from "react";
import { btn, btnSm } from "../../components/ui/buttons";
import { Dialog, DialogShell } from "../../components/ui/DialogShell";
import { card } from "./shared";

/**
 * 提醒信說明（規格書 4.9、ADR-0031）：放在行程頁送件區上方的獨立區塊，還缺需上傳的文件時才出現。
 * 「查看預設提醒信內容」打開對話框：左邊是信件範本（標色的是程式帶入的內容），右邊是範例，下面是寄送規則。
 *
 * 只有 Google 登入的使用者會收到提醒信（只寄給驗證過的信箱），所以開發登入的帳號不顯示。
 * 範本與範例的文字要和後端 `app/domain/reminder/letter.py` 一致。
 */
export function ReminderNotice({ missingUploads }: { missingUploads: number }) {
  const [open, setOpen] = useState(false);
  if (missingUploads === 0) return null;
  return (
    <section
      className={`${card} flex flex-wrap items-center gap-x-5 gap-y-3`}
      aria-label="提醒信"
      data-testid="reminder-notice"
    >
      <h2 className="m-0 text-xl font-bold text-heading">提醒信</h2>
      <div className="t-body text-ink">
        <p className="m-0">還缺文件滿 24 小時，會寄提醒信到你的 Google 信箱。</p>
        <p className="m-0">之後每 3 天一封，最多 3 封。</p>
      </div>
      <div className="ml-auto">
        <button
          type="button"
          className={btnSm}
          onClick={() => setOpen(true)}
          data-testid="reminder-template-open"
        >
          查看預設提醒信內容
        </button>
      </div>
      <ReminderTemplateDialog open={open} onOpenChange={setOpen} />
    </section>
  );
}

/** 程式帶入的內容：範本與範例用同一個顏色標出來，對照得到。 */
function Slot({ children }: { children: ReactNode }) {
  return <mark className="rounded bg-sun-soft px-0.5 text-warn-ink">{children}</mark>;
}

type Slots = {
  trip: ReactNode;
  count: ReactNode;
  name: ReactNode;
  incident: ReactNode;
  documents: ReactNode[];
  company: ReactNode;
  link?: ReactNode;
};

const TEMPLATE: Slots = {
  trip: <Slot>行程名稱（目的地＋保險期間）</Slot>,
  count: <Slot>N</Slot>,
  name: <Slot>你的 Google 帳號名稱</Slot>,
  incident: <Slot>事故類型 · 發生日</Slot>,
  documents: [<Slot key="d">還缺的文件（依事故分組；共用的文件列在「共同文件」）</Slot>],
  company: <Slot>你投保方案的保險公司</Slot>,
  link: <Slot>連到行程頁，選中第一個還缺文件的事故</Slot>,
};

const SAMPLE: Slots = {
  trip: <Slot>日本 2026/07/05～07/10</Slot>,
  count: <Slot>2</Slot>,
  name: <Slot>王小明</Slot>,
  incident: <Slot>班機取消 · 2026/07/10</Slot>,
  documents: [<Slot key="a">航空公司開立的班機取消證明</Slot>, <Slot key="b">住宿收據</Slot>],
  company: <Slot>富邦產物</Slot>,
};

function Letter({ title, slots, testId }: { title: string; slots: Slots; testId: string }) {
  return (
    <div
      className="min-w-0 rounded-2xl border border-line bg-paper p-3 t-body break-words text-ink md:p-4"
      data-testid={testId}
    >
      <p className="m-0 mb-2 t-label text-heading">{title}</p>
      <p className="m-0 t-caption text-muted">寄件者：好險有你</p>
      <p className="m-0 mb-3 t-label text-heading">
        【好險有你】{slots.trip} 還缺 {slots.count} 份理賠文件
      </p>
      <p className="m-0">{slots.name}你好，</p>
      <p className="mt-2 mb-0">你的行程「{slots.trip}」還有以下理賠文件尚未上傳：</p>
      <p className="mt-2 mb-0 font-bold">{slots.incident}</p>
      <ul className="m-0 list-none p-0">
        {slots.documents.map((d, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 固定的展示清單
          <li key={i}>・{d}</li>
        ))}
      </ul>
      <p className="mt-2 mb-0">
        上傳完成後，我們可以協助你把文件寄給{slots.company}
        ，或者你自己處理就好將文件儲存至Gmail草稿。
        <br />
        提醒：理賠申請有規定期限，應盡早準備。
      </p>
      <p className="mt-2 mb-0 t-label text-leaf-text">［前往上傳］</p>
      {slots.link && <p className="m-0 t-caption">{slots.link}</p>}
      <p className="mt-2 mb-0 t-caption text-muted">此信由系統自動寄出，請勿直接回覆。</p>
    </div>
  );
}

export function ReminderTemplateDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      title="預設提醒信內容"
      variant="wide"
      testId="reminder-template"
      actions={<Dialog.Close className={btn}>關閉</Dialog.Close>}
    >
      <p className="m-0 t-caption text-muted">
        標色的地方由系統帶入你的資料，其餘是固定文字，不經 AI 產生。
      </p>
      {/* 手機也維持左右對照 */}
      <div className="grid grid-cols-2 gap-2 md:gap-4">
        <Letter title="信件內容如何產生" slots={TEMPLATE} testId="reminder-template-source" />
        <Letter title="範例" slots={SAMPLE} testId="reminder-template-sample" />
      </div>
      <div data-testid="reminder-template-rules">
        <h3 className="m-0 mb-2 t-label text-heading">什麼時候寄</h3>
        <ul className="m-0 list-disc space-y-1 pl-5 t-body text-ink">
          <li>寄到你登入用的 Google 信箱，寄件者是「好險有你」。</li>
          <li>還缺文件滿 24 小時寄第一封，之後每 3 天一封，最多 3 封。</li>
          <li>有新狀況並新增需上傳的待辦時，從第一封重新計算。</li>
          <li>需上傳的待辦都完成（含自行勾選），或行程結案，就不再寄。</li>
        </ul>
      </div>
    </DialogShell>
  );
}
