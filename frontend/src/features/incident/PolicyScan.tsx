import { useEffect, useRef, useState } from "react";
import type { PolicyUploadOut } from "../../client/types.gen";
import { btnSm } from "../../components/ui/buttons";
import { formatSize } from "./UploadPreview";

/**
 * 上傳保單時的縮圖與保單解析過程（票 #444）：
 * - 縮圖：在瀏覽器用 pdf.js 畫第一頁，只存在這次畫面，不離開瀏覽器、不寫入任何儲存；失敗退回圖示。
 * - 步驟：後端回應回來後，依真實解析結果一步一步亮出；讀不到的欄位標「沒讀到」，不假裝成功。
 *   總長 ≤3 秒，可略過；偏好減少動態時由呼叫端直接給結果（不渲染逐步動畫）。
 * 解析本身是程式規則、一次回應就好，這裡只是把已經拿到的結果分步呈現（ADR-0027 補充）。
 */
export type ScanPhase = "uploading" | "revealing" | "done" | "failed";

/** 每步的間隔；6 步 × 450ms ≈ 2.7 秒 */
export const STEP_MS = 450;

type StepState = "pending" | "active" | "done" | "missing";
type Step = { label: string; text: string; state: "done" | "missing" };

const STEP_LABELS = {
  read: "讀取保單檔案",
  product: "辨識商品名稱與險種",
  sum: "抽出保額",
  period: "抽出保險期間與旅遊國家",
  plan: "比對方案",
  riders: "找適用的附加條款",
} as const;
const LABELS = Object.values(STEP_LABELS);
const TOTAL = LABELS.length;

const MISSING = "沒讀到，請手動補";

/** 由後端回應組出每一步（只用回應裡真實存在的資料；沒讀到的欄位標 missing）。 */
export function buildSteps(policy: PolicyUploadOut, file: File): Step[] {
  const read = new Set(policy.auto_filled);
  const periodRead = read.has("period");
  const destRead = read.has("destination");
  const period =
    policy.start_date && policy.end_date ? `${policy.start_date} 至 ${policy.end_date}` : null;
  const step2 = [
    policy.product_name,
    policy.insurances.length > 0 ? `含 ${policy.insurances.length} 項險種` : null,
  ].filter(Boolean);
  return [
    { label: STEP_LABELS.read, text: `${file.name}（${formatSize(file.size)}）`, state: "done" },
    {
      label: STEP_LABELS.product,
      text: step2.length > 0 ? step2.join("；") : MISSING,
      state: step2.length > 0 ? "done" : "missing",
    },
    {
      label: STEP_LABELS.sum,
      text:
        read.has("sum_insured") && policy.sum_insured !== null
          ? `保額 ${policy.sum_insured.toLocaleString("en-US")} 元`
          : MISSING,
      state: read.has("sum_insured") ? "done" : "missing",
    },
    {
      label: STEP_LABELS.period,
      text: [
        periodRead && period ? `保險期間 ${period}` : `保險期間 ${MISSING}`,
        destRead && policy.destination ? `旅遊國家 ${policy.destination}` : `旅遊國家 ${MISSING}`,
      ].join("；"),
      state: periodRead && destRead ? "done" : "missing",
    },
    { label: STEP_LABELS.plan, text: `${policy.company} ${policy.plan_name}`, state: "done" },
    {
      label: STEP_LABELS.riders,
      text: policy.riders.length > 0 ? policy.riders.join("、") : "保單上沒有適用的附加條款",
      state: "done",
    },
  ];
}

type ThumbState = "loading" | "canvas" | "fallback";

/** 用 pdf.js 把第一頁畫到 canvas；pdf.js 在第一次選檔時才載入，其他頁面不受影響。 */
function useThumb(file: File, canvasRef: React.RefObject<HTMLCanvasElement | null>): ThumbState {
  const [state, setState] = useState<ThumbState>("loading");
  useEffect(() => {
    let cancelled = false;
    setState("loading");
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        const task = pdfjs.getDocument({
          data: await file.arrayBuffer(),
          cMapUrl: "/pdfjs/cmaps/",
          cMapPacked: true,
          standardFontDataUrl: "/pdfjs/standard_fonts/",
        });
        const doc = await task.promise;
        try {
          const page = await doc.getPage(1);
          const base = page.getViewport({ scale: 1 });
          const scale = (160 * Math.min(window.devicePixelRatio || 1, 2)) / base.width;
          const viewport = page.getViewport({ scale });
          const canvas = canvasRef.current;
          if (cancelled || !canvas) return;
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);
          await page.render({ canvas, viewport }).promise;
          if (!cancelled) setState("canvas");
        } finally {
          void task.destroy();
        }
      } catch {
        if (!cancelled) setState("fallback");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [file, canvasRef]);
  return state;
}

// Tailwind 只認完整寫出的 class 名稱，所以橫式矮視窗的變體直接寫在字串裡
const LAYOUT =
  "grid gap-4 sm:grid-cols-[160px_1fr] [@media(orientation:landscape)_and_(max-height:500px)]:grid-cols-[96px_1fr]";
const THUMB_BOX =
  "relative w-40 overflow-hidden rounded-tag border border-line bg-white max-sm:mx-auto [@media(orientation:landscape)_and_(max-height:500px)]:w-24";

export function PolicyScan({
  file,
  phase,
  policy,
  reduced,
  onDone,
  onReselect,
}: {
  file: File;
  phase: ScanPhase;
  policy: PolicyUploadOut | null;
  reduced: boolean;
  /** 步驟亮完或按略過時呼叫一次，呼叫端這時才把值填進表單 */
  onDone: () => void;
  onReselect: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const thumb = useThumb(file, canvasRef);
  const steps = policy ? buildSteps(policy, file) : null;
  const [revealed, setRevealed] = useState(0);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  // 回應回來後（revealing）每 STEP_MS 亮一步，亮完通知呼叫端；換檔重來
  useEffect(() => {
    if (phase !== "revealing") return;
    setRevealed(0);
    const timer = window.setInterval(() => {
      setRevealed((n) => {
        if (n + 1 >= TOTAL) window.clearInterval(timer);
        return Math.min(n + 1, TOTAL);
      });
    }, STEP_MS);
    return () => window.clearInterval(timer);
  }, [phase]);

  const finished = useRef(false);
  useEffect(() => {
    if (phase === "revealing") finished.current = false;
  }, [phase]);
  useEffect(() => {
    if (phase === "revealing" && revealed >= TOTAL && !finished.current) {
      finished.current = true;
      doneRef.current();
    }
  }, [phase, revealed]);

  const shown = phase === "done" ? TOTAL : phase === "revealing" ? revealed : 0;
  const sweep =
    phase === "uploading" || phase === "revealing" ? "loop" : phase === "done" ? "once" : "off";

  function skip() {
    if (finished.current) return;
    finished.current = true;
    setRevealed(TOTAL);
    doneRef.current();
  }

  return (
    <div
      data-testid="policy-scan"
      data-motion={reduced ? "off" : "on"}
      data-phase={phase}
      className={`${LAYOUT} rounded-tile border border-line bg-white p-4`}
    >
      <div
        data-testid="policy-thumb"
        data-kind={thumb}
        className={THUMB_BOX}
        role="img"
        aria-label={`${file.name} 第一頁縮圖`}
      >
        <canvas ref={canvasRef} className={thumb === "canvas" ? "block h-auto w-full" : "hidden"} />
        {thumb !== "canvas" && (
          <div className="grid aspect-[3/4] place-items-center t-caption text-muted">
            <span aria-hidden>📄</span>
            <span className="px-2 text-center [overflow-wrap:anywhere]">{file.name}</span>
          </div>
        )}
        {!reduced && <span aria-hidden className="policy-sweep" data-run={sweep} />}
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {LABELS.map((label, i) => {
            const n = i + 1;
            const revealedStep = steps && n <= shown ? steps[i] : null;
            const state: StepState = revealedStep
              ? revealedStep.state
              : phase === "revealing" && n === shown + 1
                ? "active"
                : "pending";
            return (
              <li
                key={label}
                data-testid={`policy-step-${n}`}
                data-state={state}
                className={`flex gap-2 t-caption ${state === "pending" ? "text-muted/60" : "text-heading"}`}
              >
                <span aria-hidden className="w-4 shrink-0 text-center">
                  {state === "done"
                    ? "✓"
                    : state === "missing"
                      ? "!"
                      : state === "active"
                        ? "…"
                        : "○"}
                </span>
                <span className="min-w-0 [overflow-wrap:anywhere]">
                  <b className="t-caption-bold">{label}</b>
                  {revealedStep && (
                    <span className={state === "missing" ? " text-warn" : " text-muted"}>
                      {" "}
                      {revealedStep.text}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>

        {phase === "revealing" && !reduced && (
          <button
            type="button"
            data-testid="policy-skip"
            onClick={skip}
            className={`${btnSm} min-h-11 min-w-11 self-start`}
          >
            略過
          </button>
        )}
        {phase === "failed" && (
          <button
            type="button"
            data-testid="policy-reselect"
            onClick={onReselect}
            className={`${btnSm} min-h-11 min-w-11 self-start`}
          >
            重新選擇
          </button>
        )}
      </div>
    </div>
  );
}
