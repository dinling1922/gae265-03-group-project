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
import { Tabs } from "../../components/ui/Tabs";
import { ActivityDialog, type ActivityForm, type ActivityTarget } from "./ActivityDialog";
import { DayTabs, Timeline } from "./DayTimeline";
import { TripSidebar } from "./TripSidebar";
import { datesBetween, formatDay, formatHour, tripDays, validateTrip } from "./tripRules";
import { type TripFields, useItineraryActions } from "./useItineraryActions";

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
    comparison.city !== form.city ||
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
        setSaveError(null);
        setToRemove(null);
        return result.data.comparison;
      }
      if (result.code === "WOULD_REMOVE_ACTIVITIES") {
        setToRemove((result.detail as { activities: ActivityBrief[] }).activities);
      } else {
        setSaveError(result.message);
      }
      return null;
    };
    const next = queue.current.then(run);
    queue.current = next.catch(() => null);
    return next;
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在表單或已存的值變了時重排；save 每次渲染都是新函式
  useEffect(() => {
    if (!valid || !dirty || toRemove) return;
    const timer = setTimeout(() => void save(), SAVE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [valid, dirty, toRemove, form, comparison]);

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
    const saved = dirty ? await save() : comparison;
    if (saved) void navigate({ to: "/compare/$id/concerns", params: { id: saved.id } });
  };

  // ---- 日程：日期分頁、活動 ----
  const dates = comparison ? datesBetween(comparison.depart_date, comparison.return_date) : [];
  const [activeDate, setActiveDate] = useState<string>(dates[0] ?? "");
  const day = dates.includes(activeDate) ? activeDate : (dates[0] ?? "");
  const counts: Record<string, number> = {};
  for (const a of activities) counts[a.date] = (counts[a.date] ?? 0) + 1;
  const dayActivities = activities.filter((a) => a.date === day);

  const [target, setTarget] = useState<ActivityTarget | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function submitActivity(t: ActivityTarget, f: ActivityForm): Promise<string | null> {
    const result =
      t.kind === "add"
        ? await actions.addActivities([{ date: t.date, hour: t.hour, ...f }])
        : await actions.changeActivity(t.activity.id, f);
    if (!result.ok) return result.message;
    // 批次裡唯一的一筆失敗（時段剛好被佔用）：留在視窗顯示原因
    if ("failed" in result.data && result.data.failed[0]) return result.data.failed[0].message;
    setTarget(null);
    return null;
  }

  async function removeActivity(a: ActivityOut) {
    const result = await actions.removeActivity(a.id);
    setActionError(result.ok ? null : result.message);
  }

  return (
    <div
      data-page-paper
      data-page-wide
      className="relative left-1/2 grid w-screen -translate-x-1/2 grid-cols-1 font-ui md:grid-cols-[minmax(250px,305px)_minmax(0,1fr)] min-[1091px]:grid-cols-[minmax(255px,305px)_minmax(390px,1fr)_minmax(345px,440px)] min-[1250px]:grid-cols-[minmax(290px,335px)_minmax(430px,1fr)_minmax(365px,455px)]"
    >
      <TripSidebar
        form={form}
        onChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
        errors={errors}
        saveError={saveError}
        nextDisabled={!valid || saving}
        onNext={() => void goNext()}
      />

      <main className="flex min-w-0 flex-col gap-6 p-4 md:p-6">
        {comparison && dates.length > 0 ? (
          <>
            <TripBanner comparison={comparison} />
            <DayTabs dates={dates} value={day} onChange={setActiveDate} counts={counts}>
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
            className="rounded-panel border border-line bg-white p-6 shadow-card"
          >
            <h2 className="m-0 t-section-title text-heading">先填好旅行規劃</h2>
            <p className="m-0 mt-2 t-body text-muted">
              選好目的地、出發日與回程日，就能開始排每天的日程；不想排也可以直接按「下一步」。
            </p>
          </section>
        )}
      </main>

      <aside
        data-testid="map-panel"
        className="border-line p-4 md:col-start-2 md:border-t md:p-6 min-[1091px]:sticky min-[1091px]:top-4 min-[1091px]:col-start-3 min-[1091px]:max-h-[calc(100vh-2rem)] min-[1091px]:self-start min-[1091px]:overflow-auto min-[1091px]:border-t-0 min-[1091px]:border-l"
      />

      <ActivityDialog target={target} onClose={() => setTarget(null)} onSubmit={submitActivity} />
      <ShortenDialog
        activities={toRemove}
        onCancel={cancelShorten}
        onConfirm={() => void save(true)}
      />
    </div>
  );
}

function TripBanner({ comparison }: { comparison: ComparisonOut }) {
  const place = comparison.city
    ? `${comparison.city}，${comparison.destination}`
    : comparison.destination;
  return (
    <section
      data-testid="trip-banner"
      className="rounded-panel bg-[linear-gradient(135deg,#183b20_0%,#285442_60%,#55790f_100%)] px-6 py-6 text-white md:px-8 md:py-7"
    >
      <p className="m-0 t-caption-bold tracking-widest opacity-80">你的旅遊日程</p>
      <h2 className="m-0 mt-1 text-[30px] leading-10 font-bold md:text-[40px] md:leading-[52px]">
        {place}
      </h2>
      <p className="m-0 mt-2 inline-flex items-center gap-1.5 t-body opacity-90">
        <CalendarIcon className="size-4" />
        {formatDay(comparison.depart_date)} — {formatDay(comparison.return_date)}・
        {tripDays(comparison.depart_date, comparison.return_date)} 天
      </p>
    </section>
  );
}

/** 縮短日期會刪活動：列出會移除的活動，確認後才帶 confirm_remove 存檔。 */
function ShortenDialog({
  activities,
  onCancel,
  onConfirm,
}: {
  activities: ActivityBrief[] | null;
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
    </DialogShell>
  );
}
