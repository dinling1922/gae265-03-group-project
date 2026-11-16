import { Button } from "@base-ui/react/button";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  getComparisonOptions,
  listItineraryActivitiesOptions,
} from "../../client/@tanstack/react-query.gen";
import type {
  ActivityBrief,
  ActivityOut,
  ComparisonOut,
  Destination,
} from "../../client/types.gen";
import { CalendarIcon } from "../../components/icons";
import { btn, btnPri } from "../../components/ui/buttons";
import { Dialog, DialogShell } from "../../components/ui/DialogShell";
import { FieldError } from "../../components/ui/Field";
import { Tabs } from "../../components/ui/Tabs";
import { useToast } from "../../components/ui/Toast";
import { ActivityDialog, type ActivityForm, type ActivityTarget } from "./ActivityDialog";
import { BANNER_PHOTOS, creditHref, photoSrc } from "./bannerPhotos";
import { DayTabs, Timeline } from "./DayTimeline";
import { geocode } from "./geocode";
import { MapPanel } from "./MapPanel";
import { mapStops, useMapView } from "./mapView";
import { ToolsDialog } from "./ToolsDialog";
import { TripSidebar } from "./TripSidebar";
import { datesBetween, formatDay, formatHour, tripDays, validateTrip } from "./tripRules";
import { type TripFields, useItineraryActions } from "./useItineraryActions";
import { useItineraryWebMCP } from "./useItineraryWebMCP";

const SAVE_DELAY_MS = 500;

/**
 * 旅遊日程頁（ADR-0031）：取代 1-1。`id` 是 undefined 時是 `/compare/new` 的空白頁，
 * 目的地與日期都有值那一刻建立比較紀錄、網址換成 `/compare/$id/itinerary`；之後的修改即時存檔。
 */
export function ItineraryPage({ id }: { id?: string }) {
  if (!id) return <Itinerary key="new" activities={[]} />;
  return <LoadedItinerary id={id} />;
}

function LoadedItinerary({ id }: { id: string }) {
  const comparison = useQuery(getComparisonOptions({ path: { comparison_id: id } }));
  const activities = useQuery(listItineraryActivitiesOptions({ path: { comparison_id: id } }));
  if (comparison.error) {
    return (
      <p role="alert" className="text-red-600">
        找不到這筆比較紀錄。
      </p>
    );
  }
  // 表單只用讀回的值初始化一次：等這次進頁面的讀取完成，不拿快取裡可能過期的資料
  if (!comparison.isFetchedAfterMount || !comparison.data || !activities.data) {
    return <p>讀取中…</p>;
  }
  return <Itinerary key={id} comparison={comparison.data} activities={activities.data} />;
}

function Itinerary({
  comparison,
  activities,
}: {
  comparison?: ComparisonOut;
  activities: ActivityOut[];
}) {
  const navigate = useNavigate();
  const actions = useItineraryActions(comparison?.id);

  // ---- 旅行規劃：表單欄位、即時存檔 ----
  const [form, setForm] = useState({
    destination: comparison?.destination ?? "",
    city: comparison?.city ?? "",
    depart: comparison?.depart_date ?? "",
    returnDate: comparison?.return_date ?? "",
  });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [toRemove, setToRemove] = useState<ActivityBrief[] | null>(null);
  const [saving, setSaving] = useState(false);
  // 存檔排成一串：上一次還沒回來就不送下一次，避免回應亂序
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const errors = validateTrip(form.depart, form.returnDate, comparison?.depart_date);
  const valid = Boolean(form.destination && form.depart && form.returnDate) && errors.length === 0;
  const dirty =
    !comparison ||
    comparison.destination !== form.destination ||
    comparison.city !== form.city.trim() ||
    comparison.depart_date !== form.depart ||
    comparison.return_date !== form.returnDate;

  const fields = (): TripFields => ({
    destination: form.destination as Destination,
    city: form.city.trim(),
    depart_date: form.depart,
    return_date: form.returnDate,
  });

  /** 存目前的表單；回傳存好的比較紀錄，失敗或要先確認時回 null。 */
  function save(confirmRemove = false): Promise<ComparisonOut | null> {
    const snapshot = fields();
    const run = async () => {
      setSaving(true);
      const result = await actions.saveTrip(snapshot, confirmRemove);
      setSaving(false);
      if (result.ok) {
        failed.current = null;
        setSaveError(null);
        setToRemove(null);
        return result.data.comparison;
      }
      if (result.code === "WOULD_REMOVE_ACTIVITIES") {
        setToRemove((result.detail as { activities: ActivityBrief[] }).activities);
      } else {
        failed.current = JSON.stringify(snapshot);
        setSaveError(result.message);
      }
      return null;
    };
    const next = queue.current.then(run);
    queue.current = next.catch(() => null);
    return next;
  }

  // 存檔的等待計時：按「下一步」時要取消，不然計時到了會再存一次（還沒有比較紀錄時就是再建立一筆）
  // 上次失敗的表單內容：沒改過就不自動重送（不然 saving 一變回 false 就會每 500ms 重試）
  const failed = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在表單或已存的值變了時重排；save 每次渲染都是新函式
  useEffect(() => {
    if (!valid || !dirty || toRemove || saving) return;
    if (failed.current === JSON.stringify(fields())) return;
    timer.current = setTimeout(() => void save(), SAVE_DELAY_MS);
    return () => clearTimeout(timer.current);
  }, [valid, dirty, toRemove, saving, form, comparison]);

  // agent 縮短日期會刪活動時，用同一個確認視窗等使用者按；這裡存等待中的結果（沒人等時是 null）
  const shortenAsk = useRef<((approved: boolean) => void) | null>(null);
  function answerShorten(approved: boolean) {
    shortenAsk.current?.(approved);
    setToRemove(null);
  }
  function askShorten(list: ActivityBrief[]): Promise<boolean> {
    shortenAsk.current?.(false);
    return new Promise((resolve) => {
      const timer = setTimeout(() => answerShorten(false), 120_000);
      shortenAsk.current = (approved) => {
        clearTimeout(timer);
        shortenAsk.current = null;
        resolve(approved);
      };
      setToRemove(list);
    });
  }

  const cancelShorten = () => {
    setToRemove(null);
    if (comparison) {
      setForm({
        destination: comparison.destination,
        city: comparison.city,
        depart: comparison.depart_date,
        returnDate: comparison.return_date,
      });
    }
  };

  const goNext = async () => {
    clearTimeout(timer.current);
    const saved = dirty ? await save() : comparison;
    if (saved) void navigate({ to: "/compare/$id/concerns", params: { id: saved.id } });
  };

  // ---- 日程：日期分頁、活動 ----
  const dates = comparison ? datesBetween(comparison.depart_date, comparison.return_date) : [];
  const view = useMapView(dates, activities);
  const { day } = view;
  const toast = useToast();
  const [toolsOpen, setToolsOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  // agent 要清除活動時，畫面上開確認視窗等使用者按；這裡存等待中的結果（沒人等時是 null）
  const clearAsk = useRef<((approved: boolean) => void) | null>(null);
  function closeClear(approved: boolean) {
    clearAsk.current?.(approved);
    setClearOpen(false);
  }
  function askClear(): Promise<boolean> {
    clearAsk.current?.(false);
    return new Promise((resolve) => {
      const timer = setTimeout(() => closeClear(false), 120_000);
      clearAsk.current = (approved) => {
        clearTimeout(timer);
        clearAsk.current = null;
        resolve(approved);
      };
      setClearOpen(true);
    });
  }
  const { supported } = useItineraryWebMCP({
    comparison,
    activities,
    view,
    actions,
    syncForm: (c) =>
      setForm({
        destination: c.destination,
        city: c.city,
        depart: c.depart_date,
        returnDate: c.return_date,
      }),
    toast: toast.show,
    askClear,
    askShorten,
  });
  const { stops, unmapped } = mapStops(dates, activities, view.scope, day);
  const counts: Record<string, number> = {};
  for (const a of activities) counts[a.date] = (counts[a.date] ?? 0) + 1;
  const dayActivities = activities.filter((a) => a.date === day);

  const [target, setTarget] = useState<ActivityTarget | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function submitActivity(t: ActivityTarget, f: ActivityForm): Promise<string | null> {
    // 地點填了就查座標上地圖；編輯時地點沒改就保留原座標，改了而查不到就清掉舊座標
    const old = t.kind === "edit" ? t.activity : null;
    const moved = !old || f.location !== old.location;
    const coords = moved ? await geocode(f.location, comparison?.city) : null;
    const result =
      t.kind === "add"
        ? await actions.addActivities([{ date: t.date, hour: t.hour, ...f, ...coords }])
        : await actions.changeActivity(t.activity.id, {
            ...f,
            ...(moved && (coords ?? { latitude: null, longitude: null })),
          });
    if (!result.ok) return result.message;
    // 批次裡唯一的一筆失敗（時段剛好被佔用）：留在視窗顯示原因
    if ("failed" in result.data && result.data.failed[0]) return result.data.failed[0].message;
    setTarget(null);
    return null;
  }

  async function clearAll() {
    const result = await actions.clearActivities();
    setActionError(result.ok ? null : result.message);
    if (result.ok) setClearOpen(false);
  }

  async function removeActivity(a: ActivityOut) {
    const result = await actions.removeActivity(a.id);
    setActionError(result.ok ? null : result.message);
  }

  return (
    <div
      data-page-paper
      data-page-wide
      className="relative left-1/2 grid w-screen -translate-x-1/2 grid-cols-1 font-ui min-[768px]:grid-cols-[minmax(250px,305px)_minmax(0,1fr)] min-[860px]:grid-cols-[minmax(220px,260px)_minmax(300px,1fr)_minmax(300px,380px)] min-[1091px]:grid-cols-[minmax(255px,305px)_minmax(390px,1fr)_minmax(345px,440px)] min-[1250px]:grid-cols-[minmax(290px,335px)_minmax(430px,1fr)_minmax(365px,455px)]"
    >
      <TripSidebar
        form={form}
        onChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
        errors={errors}
        saveError={saveError}
        onOpenTools={() => setToolsOpen(true)}
        activityCount={activities.length}
        onClear={() => setClearOpen(true)}
        nextDisabled={!valid || saving}
        onNext={() => void goNext()}
      />

      <main className="flex min-w-0 flex-col gap-6 p-4 pt-0 min-[768px]:p-6 min-[768px]:pt-0">
        {comparison && dates.length > 0 ? (
          <>
            <DayTabs
              dates={dates}
              value={day}
              onChange={view.showDay}
              counts={counts}
              header={<TripBanner comparison={comparison} />}
            >
              {dates.map((d, i) => (
                <Tabs.Panel key={d} value={d}>
                  <div className="mt-5 flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <p className="m-0 t-caption-bold text-muted">
                        第 {i + 1} 天・{formatDay(d)}
                      </p>
                      <h2 className="m-0 t-section-title text-heading">這一天的安排</h2>
                    </div>
                    <span className="t-caption text-muted">{counts[d] ?? 0} 個活動</span>
                  </div>
                  {actionError && (
                    <p role="alert" className="m-0 mt-2 t-caption-bold text-danger">
                      {actionError}
                    </p>
                  )}
                  <Timeline
                    activities={d === day ? dayActivities : []}
                    selectedId={view.selected?.id ?? null}
                    onSelect={view.select}
                    onAdd={(hour) => setTarget({ kind: "add", date: d, hour })}
                    onEdit={(activity) => setTarget({ kind: "edit", activity })}
                    onRemove={(activity) => void removeActivity(activity)}
                  />
                </Tabs.Panel>
              ))}
            </DayTabs>
            <p className="m-0 text-center t-caption text-muted">
              行程不用排滿，留點空檔給意外的驚喜。
            </p>
          </>
        ) : (
          <section
            data-testid="itinerary-empty"
            className="mt-4 rounded-panel border border-line bg-white p-6 shadow-card min-[768px]:mt-6"
          >
            <h2 className="m-0 t-section-title text-heading">先填好旅行規劃</h2>
            <p className="m-0 mt-2 t-body text-muted">
              選好目的地、出發日與回程日，就能開始排每天的日程；不想排也可以直接按「下一步」。
            </p>
          </section>
        )}
      </main>

      <aside className="border-line p-4 min-[768px]:p-6 min-[768px]:col-start-2 min-[768px]:border-t min-[860px]:col-start-3 min-[860px]:flex min-[860px]:flex-col min-[860px]:pb-0 min-[860px]:border-t-0 min-[860px]:border-l">
        {comparison && dates.length > 0 && (
          <MapPanel
            dates={dates}
            scope={view.scope}
            day={day}
            stops={stops}
            unmapped={unmapped}
            selectedId={view.selected?.id ?? null}
            onShowTrip={view.showTrip}
            onShowDay={view.showDay}
            onSelect={(stop) => view.select(stop.activity)}
          />
        )}
      </aside>

      <ToolsDialog open={toolsOpen} onOpenChange={setToolsOpen} supported={supported} />
      <ActivityDialog target={target} onClose={() => setTarget(null)} onSubmit={submitActivity} />
      <ClearDialog
        count={clearOpen ? activities.length : null}
        error={actionError}
        onCancel={() => closeClear(false)}
        onConfirm={() => (clearAsk.current ? closeClear(true) : void clearAll())}
      />
      <ShortenDialog
        activities={toRemove}
        error={saveError}
        onCancel={() => (shortenAsk.current ? answerShorten(false) : cancelShorten())}
        onConfirm={() => (shortenAsk.current ? answerShorten(true) : void save(true))}
      />
    </div>
  );
}

function TripBanner({ comparison }: { comparison: ComparisonOut }) {
  const place = comparison.city
    ? `${comparison.city}，${comparison.destination}`
    : comparison.destination;
  const photo = BANNER_PHOTOS[comparison.destination];
  return (
    <section
      data-testid="trip-banner"
      className="relative overflow-hidden rounded-panel bg-[linear-gradient(135deg,#183b20_0%,#285442_60%,#55790f_100%)] px-6 py-6 text-white md:px-8 md:py-7"
    >
      {photo && (
        <>
          <img
            data-testid="trip-banner-photo"
            src={photoSrc(photo.url, 1200, 400)}
            srcSet={`${photoSrc(photo.url, 640, 400)} 640w, ${photoSrc(photo.url, 1200, 400)} 1200w`}
            sizes="(min-width: 768px) 1200px, 640px"
            alt=""
            className="absolute inset-0 size-full object-cover"
          />
          {/* 中性的淡黑遮罩，白字才讀得清楚 */}
          <div className="absolute inset-0 bg-black/20" />
        </>
      )}
      <div className="relative">
        <p className="m-0 t-caption-bold tracking-widest opacity-80">你的旅遊日程</p>
        <h2 className="m-0 mt-1 text-[30px] leading-10 font-bold md:text-[40px] md:leading-[52px]">
          {place}
        </h2>
        <p className="m-0 mt-2 inline-flex items-center gap-1.5 t-body opacity-90">
          <CalendarIcon className="size-4" />
          {formatDay(comparison.depart_date)} — {formatDay(comparison.return_date)}・
          {tripDays(comparison.depart_date, comparison.return_date)} 天
        </p>
      </div>
      {photo?.credit && (
        <p
          data-testid="trip-banner-credit"
          className="absolute right-3 bottom-2 m-0 text-xs opacity-80"
        >
          Photo by{" "}
          <a
            href={creditHref(photo.credit.username)}
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            {photo.credit.name}
          </a>{" "}
          on{" "}
          <a
            href="https://unsplash.com/?utm_source=gae265_travel_insurance&utm_medium=referral"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            Unsplash
          </a>
        </p>
      )}
    </section>
  );
}

/** 縮短日期會刪活動：列出會移除的活動，確認後才帶 confirm_remove 存檔。 */
function ClearDialog({
  count,
  error,
  onCancel,
  onConfirm,
}: {
  count: number | null;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <DialogShell
      open={count !== null}
      onOpenChange={(open) => !open && onCancel()}
      title={`將移除全部 ${count ?? 0} 個活動`}
      description="這趟旅行排好的活動會全部刪除，無法復原；目的地與日期不變。"
      testId="clear-dialog"
      actions={
        <>
          <Dialog.Close className={btn}>取消</Dialog.Close>
          <Button className={btnPri} onClick={onConfirm} data-testid="clear-confirm">
            確認清除
          </Button>
        </>
      }
    >
      <FieldError>{error}</FieldError>
    </DialogShell>
  );
}

function ShortenDialog({
  activities,
  error,
  onCancel,
  onConfirm,
}: {
  activities: ActivityBrief[] | null;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <DialogShell
      open={activities !== null}
      onOpenChange={(open) => !open && onCancel()}
      title={`將移除 ${activities?.length ?? 0} 個活動`}
      description="新的日期範圍外還有這些活動，確認後會一起刪除。"
      testId="shorten-dialog"
      actions={
        <>
          <Dialog.Close className={btn}>取消</Dialog.Close>
          <Button className={btnPri} onClick={onConfirm}>
            確認移除
          </Button>
        </>
      }
    >
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0 t-body">
        {activities?.map((a) => (
          <li key={a.id}>
            {formatDay(a.date)} {formatHour(a.hour)}「{a.title}」
          </li>
        ))}
      </ul>
      <FieldError>{error}</FieldError>
    </DialogShell>
  );
}
