import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { deleteTripDocument, patchTripTodo, sendIncidentMessage } from "../../client";
import type { DocumentView, TodoView, TripOut } from "../../client/types.gen";
import { ActionBar } from "../../components/ui/ActionBar";
import { btn, btnPri, btnSm } from "../../components/ui/buttons";
import { Dialog, DialogShell } from "../../components/ui/DialogShell";
import { Pill, ProgressPill } from "../../components/ui/Tag";
import { GoogleAuthResult } from "../google/GoogleAuthResult";
import { type ChatLine, DescribeChat } from "../incident/DescribeChat";
import { card, errorText, policyMeta, tripTitle, useIncident, wideMain } from "../incident/shared";
import { TripIncidentCard } from "./TripIncidentCard";
import { TripSendBox } from "./TripSendBox";
import { TripTodoCards, tripProgress } from "./TripTodoCards";
import { TripUploader } from "./TripUploader";
import { useTrip } from "./useTrip";

const H2 = "m-0 t-section-title text-heading";
// 手機橫式（高度 500px 以下）：問答面板改成貼右的整高欄位。下方滑出 50dvh 在橫式只剩約 170px，
// 聊天區被壓到 58px、內容溢出蓋住說明文字，輸入框要在面板裡再捲（#410）
// Tailwind 只認原始碼裡寫完整的 class 名稱，不能用變數拼接
const QA_SIDE_PANEL =
  "max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:inset-y-0 max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:left-auto max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:right-0 max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:h-auto max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:w-[min(45vw,460px)] max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:rounded-t-none max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:rounded-l-[20px]";
const QA_SIDE_PAGE =
  "max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:pb-0 max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:pr-[calc(min(45vw,460px)+16px)]";
const QA_SIDE_HANDLE = "max-lg:[@media(orientation:landscape)_and_(max-height:500px)]:hidden";

// 捲動自動切換（#385）：閱讀線在視窗上緣下方這麼多 px（按「問答」時卡片對齊在 24px，留一點餘地）；
// 程式觸發的捲動最多等這麼久（ms）才放開自動切換
const READING_LINE = 48;
const AUTO_SCROLL_MS = 1200;

/**
 * 行程頁（規格 2-3，ADR-0029）：一張保單的所有事故收在一頁——事故卡、重大注意事項、待辦（依事故分組加
 * 「整個行程」的共同文件）、上傳框（整個行程一個）、問答（右欄，依選中的事故分開）。送件在後續版本接上。
 * 行程結案（送件或結案）後唯讀。
 */
export function TripPage({
  policyId,
  trip,
  googleUser,
  initialIncidentId,
}: {
  policyId: string;
  trip: TripOut;
  googleUser: boolean;
  /** 從某個事故導過來（`?incident=`）：預設選中並展開它；不在這個行程裡就忽略 */
  initialIncidentId?: string;
}) {
  const { setTrip, refresh } = useTrip(policyId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 選中的事故一定展開；其他的按「▸」展開，展開過的維持展開（規格 7.3.1）
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  // 正在等回覆的問題屬於哪個事故：切到別的事故時，不在別人的對話裡顯示
  const [asking, setAsking] = useState<{ id: string; content: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 每個事故的問答分開：輸入框裡打到一半的字也各存各的（規格 7.3.5）
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // 按「問答」後左欄要捲到哪個事故（規格 7.3：該事故總結頂端對齊問答框頂端）
  const [scrollTo, setScrollTo] = useState<string | null>(null);
  // 剛問完的事故：快取裡可能還是舊的行程（還沒有這個事故），資料更新後才找得到，所以用 effect 套用一次
  const appliedInitial = useRef<string | null>(null);
  // 程式觸發的捲動進行中（按「問答」、從事故導過來）：這段時間不自動切換問答
  const programmaticScroll = useRef(false);
  const summaryRef = useRef<HTMLElement>(null); // 「狀況總結」區塊：捲動自動切換以它為範圍
  const [qaOpen, setQaOpen] = useState(false); // 只在手機有意義：問答面板是否拉開
  const [undoing, setUndoing] = useState<TodoView | null>(null);
  const [ticking, setTicking] = useState<TodoView | null>(null);
  const [deleting, setDeleting] = useState<DocumentView | null>(null);

  const locked = trip.locked;
  // 預設選中發生日期最早的事故（事故已依發生時間排序）
  const selected = trip.incidents.find((i) => i.id === selectedId) ?? trip.incidents[0];
  const { data: ctx, refresh: refreshIncident } = useIncident(selected?.id ?? "");
  const question = drafts[selected?.id ?? ""] ?? "";
  // biome-ignore lint/correctness/useExhaustiveDependencies: 同一個 initialIncidentId 只套用一次
  useEffect(() => {
    if (!initialIncidentId || appliedInitial.current === initialIncidentId) return;
    if (!trip.incidents.some((i) => i.id === initialIncidentId)) return;
    appliedInitial.current = initialIncidentId;
    setSelectedId(initialIncidentId);
    setExpanded((prev) => new Set(prev).add(initialIncidentId));
    // 導過來的事故不是第一個時，捲到它
    if (initialIncidentId !== trip.incidents[0]?.id) setScrollTo(initialIncidentId);
  }, [initialIncidentId, trip.incidents]);
  const { done, total } = tripProgress(trip);

  // 事故卡展開、面板拉開後再捲：先等這一輪畫面更新完。問答框固定在上方（top-6），事故卡留同樣的上緣
  useEffect(() => {
    if (!scrollTo) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // 這一捲是程式觸發的：捲動期間途中經過的卡片不能搶走選中（#385）。放開的時機：捲完（scrollend）而且
    // 目標卡片已經到閱讀線上，或逾時。只看 scrollend 不夠：事故卡展開時瀏覽器會先做一次版面微調的捲動，
    // 它的 scrollend 比平滑捲動早，太早放開會讓途中的卡片把選中搶走，平滑捲動也跟著被打斷
    programmaticScroll.current = true;
    const target = scrollTo;
    const release = () => {
      programmaticScroll.current = false;
      window.removeEventListener("scrollend", onScrollEnd);
      window.clearTimeout(timer);
    };
    const onScrollEnd = () => {
      const top = document
        .querySelector(`[data-incident-id="${target}"]`)
        ?.getBoundingClientRect().top;
      if (top === undefined || top <= READING_LINE) release();
    };
    const timer = window.setTimeout(release, reduce ? 100 : AUTO_SCROLL_MS);
    window.addEventListener("scrollend", onScrollEnd);
    document
      .querySelector(`[data-incident-id="${scrollTo}"]`)
      ?.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
    setScrollTo(null);
  }, [scrollTo]);

  // 左欄捲到哪一個事故的狀況總結，右欄問答就換成那個事故（#385）。只改選中的事故，不拉開面板、不捲動；
  // 看過的卡片維持展開（和按「問答」一樣加進 expanded），否則離開的卡片收起，下方整個往上跳，會連續觸發切換。
  // 下面的情況不切：輸入框有字、正在等回覆、手機問答面板拉開、程式觸發的捲動期間。
  const autoSwitch = useRef({ selectedId: "", hold: false });
  autoSwitch.current = {
    selectedId: selected?.id ?? "",
    hold:
      locked ||
      question !== "" ||
      busy ||
      asking !== null ||
      (qaOpen && !window.matchMedia?.("(min-width: 1024px)").matches),
  };
  const manyIncidents = trip.incidents.length > 1;
  useEffect(() => {
    if (!manyIncidents) return;
    let frame = 0;
    const pick = () => {
      frame = 0;
      if (programmaticScroll.current || autoSwitch.current.hold) return;
      // 以「狀況總結」整個區塊為範圍：閱讀線還在區塊內，取上緣已過線的最後一張卡片（卡片之間的空隙算前一張；
      // 快速捲動時被跳過的矮卡片也不會漏）；線在第一張之上（含頁面最上方）取第一張；
      // 線已經離開區塊（重大注意事項、理賠待辦、送件）不切
      const cards = [...document.querySelectorAll<HTMLElement>("[data-incident-id]")];
      const summary = summaryRef.current;
      if (!summary || cards.length === 0 || summary.getBoundingClientRect().bottom <= READING_LINE)
        return;
      let current = cards[0];
      for (const el of cards) {
        if (el.getBoundingClientRect().top > READING_LINE) break;
        current = el;
      }
      const id = current?.dataset.incidentId;
      if (id && id !== autoSwitch.current.selectedId) {
        setSelectedId(id);
        setExpanded((prev) => new Set(prev).add(id));
      }
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(pick);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [manyIncidents]);
  const hasUpload = [...trip.shared_todos, ...trip.incidents.flatMap((i) => i.todos)].some(
    (t) => t.requires_upload,
  );

  async function toggle(todo: TodoView, checked: boolean) {
    setBusy(true);
    setError(null);
    setUndoing(null);
    setTicking(null);
    const res = await patchTripTodo({
      path: { policy_id: policyId, todo_id: todo.id },
      body: { is_done: checked },
    });
    setBusy(false);
    if (!res.data) return setError(errorText(res.error, res.response?.status));
    setTrip(res.data);
  }

  async function removeDocument(document: DocumentView) {
    setBusy(true);
    setError(null);
    const res = await deleteTripDocument({
      path: { policy_id: policyId, document_id: document.id },
    });
    setBusy(false);
    setDeleting(null);
    if (!res.data) return setError(errorText(res.error, res.response?.status));
    setTrip(res.data);
  }

  function onToggle(todo: TodoView, checked: boolean) {
    if (todo.done_by === "agent" && !checked) return setUndoing(todo);
    const hasFile = [...trip.shared_documents, ...trip.incidents.flatMap((i) => i.documents)].some(
      (d) => d.todo_id === todo.id,
    );
    if (checked && todo.requires_upload && !hasFile) return setTicking(todo);
    void toggle(todo, checked);
  }

  async function ask(content: string) {
    if (!selected || !content.trim() || busy) return;
    setBusy(true);
    setAsking({ id: selected.id, content });
    setError(null);
    const res = await sendIncidentMessage({
      path: { incident_id: selected.id },
      body: { content, input_mode: "text" },
    });
    if (!res.data) {
      setAsking(null);
      setBusy(false);
      return setError(errorText(res.error, res.response?.status));
    }
    // 回應是送出當下的快照（5 號回覆可能很慢），改成重新讀取；問答可能改了待辦，行程也一起讀
    await Promise.all([refresh(), refreshIncident()]);
    setDrafts((prev) => ({ ...prev, [selected.id]: "" }));
    setAsking(null);
    setBusy(false);
  }

  const lines: ChatLine[] = (ctx?.messages ?? []).map((m) => ({
    from: m.role === "user" ? "me" : "ai",
    text: m.content,
    citations: m.citations,
  }));
  const lastMessage = ctx?.messages?.at(-1);
  if (
    asking &&
    asking.id === selected?.id &&
    !(lastMessage?.role === "user" && lastMessage.content === asking.content)
  ) {
    lines.push({ from: "me", text: asking.content });
  }

  function select(id: string, openPanel: boolean) {
    setSelectedId(id);
    setExpanded((prev) => new Set(prev).add(id));
    if (openPanel) {
      setQaOpen(true);
      setScrollTo(id);
    }
  }

  return (
    <main
      data-page-paper
      className={`mx-auto flex flex-col gap-6 font-ui md:px-6 ${wideMain} ${
        qaOpen && !locked ? `max-lg:pb-[52dvh] ${QA_SIDE_PAGE}` : ""
      }`}
    >
      <p className="t-label m-0 text-muted">出事了：幫我處理理賠</p>

      <section
        className={`${card} flex flex-wrap items-center gap-x-6 gap-y-2`}
        aria-label="行程狀態"
      >
        <h1 className="m-0 t-section-title text-heading" data-testid="trip-title">
          {tripTitle(trip.destination, trip.policy.start_date, trip.policy.end_date)}
        </h1>
        <span className="t-body text-muted">
          {policyMeta(trip.policy.plan_name, trip.policy.sum_insured)}
        </span>
        {total > 0 && (
          <ProgressPill>
            待辦 {done}／{total}
          </ProgressPill>
        )}
        {locked && <Pill tone="closed">已結案</Pill>}
      </section>

      {locked && (
        <p
          className="m-0 rounded-btn bg-paper px-4 py-2 t-caption text-muted"
          data-testid="locked-note"
        >
          這個行程已送件結案，不能再新增狀況。如果還要補送，請重新建立行程。
          <Link to="/incident/new" className="ml-1 underline" data-testid="new-trip">
            建立新行程
          </Link>
        </p>
      )}
      <GoogleAuthResult />
      {error && (
        <p
          role="alert"
          className="m-0 rounded-btn bg-danger-soft px-4 py-2 t-caption-bold text-danger-ink"
          data-testid="error"
        >
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[7fr_5fr] lg:items-start">
        <div className="flex flex-col gap-6 lg:col-start-1 lg:row-start-1">
          <section ref={summaryRef} className="flex flex-col gap-4" aria-label="狀況總結">
            <h2 className={H2}>狀況總結</h2>
            {trip.incidents.map((incident) => (
              <TripIncidentCard
                key={incident.id}
                incident={incident}
                selected={incident.id === selected?.id}
                expanded={expanded.has(incident.id)}
                locked={locked}
                onAsk={() => select(incident.id, true)}
                onToggle={() =>
                  setExpanded((prev) => {
                    const next = new Set(prev);
                    if (!next.delete(incident.id)) next.add(incident.id);
                    return next;
                  })
                }
              />
            ))}
            <p className="m-0 t-caption text-muted">
              以上為可能適用的項目，實際以保險公司審核為準。
            </p>
          </section>

          {trip.major_notices.length > 0 && (
            <section
              className={`${card} border-l-6 border-l-sun`}
              aria-label="重大注意事項"
              data-testid="major-notices-card"
            >
              <h3 className="m-0 t-subsection-title text-heading">重大注意事項</h3>
              <ol
                className="m-0 mt-2 flex list-decimal flex-col gap-1 pl-5.5"
                data-testid="major-notices"
              >
                {trip.major_notices.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ol>
            </section>
          )}

          <section className="flex flex-col gap-4" aria-label="理賠待辦">
            <h2 className={H2}>理賠待辦</h2>
            {total === 0 ? (
              <p className="m-0 t-body text-muted" data-testid="no-todos">
                目前沒有待辦：沒有「可能適用」的保障項目，或還有項目在待確認事項，需要補充資訊後才會產生待辦。
              </p>
            ) : (
              <>
                {!locked && !googleUser && hasUpload && (
                  <p className="m-0 rounded-btn bg-paper px-4 py-2 t-caption text-muted">
                    你不是用 Google 登入，文件無法存到 Google Drive，之後也不能用 Gmail 寄出送件信。
                  </p>
                )}
                {!locked && hasUpload && (
                  <TripUploader
                    policyId={policyId}
                    trip={trip}
                    googleUser={googleUser}
                    onError={setError}
                  />
                )}
                {!locked && done === total && (
                  <p
                    className="m-0 rounded-tile bg-leaf-soft px-5 py-4 t-label text-heading"
                    data-testid="all-done"
                  >
                    所有待辦都完成了，可以送件。
                  </p>
                )}
                <TripTodoCards
                  trip={trip}
                  disabled={busy || locked}
                  onToggle={onToggle}
                  onDeleteDocument={setDeleting}
                />
              </>
            )}
          </section>

          <TripSendBox policyId={policyId} trip={trip} />
        </div>

        {/* 手機：按事故卡的「問答」，該事故的問答面板從下方滑出（約占下半）；上方待辦仍可捲動，所以沒有遮罩。
            桌機是固定的右欄 */}
        {!locked && selected && (
          <section
            id="trip-qa"
            className={`flex flex-col gap-4 lg:sticky lg:top-6 lg:col-start-2 lg:row-start-1 max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:z-40 max-lg:h-[50dvh] max-lg:overflow-y-auto max-lg:rounded-t-[20px] max-lg:bg-paper max-lg:px-4 max-lg:pt-2 max-lg:pb-5 max-lg:shadow-dialog ${QA_SIDE_PANEL} ${
              qaOpen ? "" : "max-lg:hidden"
            }`}
            aria-label="問答"
          >
            <span
              aria-hidden="true"
              className={`mx-auto mt-1 block h-[5px] w-11 rounded-sm bg-line lg:hidden ${QA_SIDE_HANDLE}`}
            />
            <div className="flex items-center gap-3">
              <h2 className={H2} data-testid="qa-title">
                問答 · {selected.incident_type_name ?? "事故"}
              </h2>
              <button
                type="button"
                onClick={() => setQaOpen(false)}
                data-testid="qa-close"
                className={`${btnSm} ml-auto lg:hidden`}
              >
                收起
              </button>
            </div>
            <DescribeChat
              lines={lines}
              quickReplies={[]}
              placeholder="關於這個事故的任何問題"
              value={question}
              onChange={(text) =>
                selected && setDrafts((prev) => ({ ...prev, [selected.id]: text }))
              }
              busy={busy}
              typing={asking?.id === selected.id}
              typingLabel="正在查閱條款"
              onSend={(content) => void ask(content)}
              maxLength={300}
              minHeight={96}
              tall
              fill
              logFooter={
                <Link
                  to="/incident/new/$policyId"
                  params={{ policyId }}
                  className={btnSm}
                  data-testid="new-incident"
                >
                  ＋ 有新狀況
                </Link>
              }
            />
            <p className="m-0 t-caption text-muted">
              回答由 AI 依條款整理，實際以保險公司審核為準。
            </p>
          </section>
        )}
      </div>

      <ActionBar>
        <ActionBar.Push>
          <Link to="/" className={btn}>
            完成
          </Link>
        </ActionBar.Push>
      </ActionBar>

      <DialogShell
        open={ticking !== null}
        onOpenChange={(open) => !open && setTicking(null)}
        title="尚未確認文件，確定要打勾嗎？"
        description={
          <p>
            這項待辦的文件還沒有上傳。打勾後會標記「未附文件」，送件的信裡會寫「另行提供」，只附上已上傳的文件。
          </p>
        }
        testId="tick-dialog"
        actions={
          <>
            <Dialog.Close className={btn}>回待辦清單</Dialog.Close>
            <button
              type="button"
              className={btnPri}
              data-testid="tick-confirm"
              onClick={() => ticking && void toggle(ticking, true)}
            >
              我要打勾
            </button>
          </>
        }
      />
      <DialogShell
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="刪除這份文件？"
        description={
          <p>
            「{deleting?.file_name}」會從這個待辦移除，也會從你的 Google Drive
            刪除，刪掉就拿不回來。 如果它是這項待辦唯一的文件，待辦會回到「待上傳」。
          </p>
        }
        testId="delete-document-dialog"
        actions={
          <>
            <Dialog.Close className={btn}>保留</Dialog.Close>
            <button
              type="button"
              className={btnPri}
              data-testid="delete-document-confirm"
              onClick={() => deleting && void removeDocument(deleting)}
            >
              刪除文件
            </button>
          </>
        }
      />
      <DialogShell
        open={undoing !== null}
        onOpenChange={(open) => !open && setUndoing(null)}
        title="取消這個待辦？"
        description={<p>這項是系統看過你的文件後確認的，確定要取消？</p>}
        testId="undo-dialog"
        actions={
          <>
            <Dialog.Close className={btn}>保留</Dialog.Close>
            <button
              type="button"
              className={btn}
              onClick={() => undoing && void toggle(undoing, false)}
            >
              確定取消
            </button>
          </>
        }
      />
    </main>
  );
}
