import { Button } from "@base-ui/react/button";
import { type ReactNode, useEffect, useRef } from "react";
import type { ActivityOut } from "../../client/types.gen";
import { ClockIcon, CloseIcon, PencilIcon, PinIcon } from "../../components/icons";
import { Tab, TabList, Tabs } from "../../components/ui/Tabs";
import { CATEGORIES } from "./categories";
import { formatDay, formatHour, HOURS } from "./tripRules";

const tag = "inline-block rounded-[6px] px-2 t-micro leading-5";
const iconBtn =
  "grid size-8 place-items-center rounded-lg text-muted hover:bg-leaf-soft hover:text-brand focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf";

/** 日期分頁：每天一頁籤，標示第幾天、日期與活動數。 */
export function DayTabs({
  dates,
  value,
  onChange,
  counts,
  children,
}: {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
  counts: Record<string, number>;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // 目前的日期分頁要看得到：換日期（含從地圖、清單選到別天）時只捲動分頁列本身，不動整頁
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在選取的日期變時捲動
  useEffect(() => {
    const list = ref.current?.querySelector<HTMLElement>('[role="tablist"]');
    const tab = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!list || !tab) return;
    const l = list.getBoundingClientRect();
    const t = tab.getBoundingClientRect();
    if (t.left < l.left) list.scrollLeft -= l.left - t.left;
    else if (t.right > l.right) list.scrollLeft += t.right - l.right;
  }, [value]);
  return (
    <Tabs.Root ref={ref} value={value} onValueChange={(v) => onChange(String(v))}>
      <TabList>
        {dates.map((d, i) => (
          <Tab key={d} value={d}>
            <span className="block t-micro text-muted">第 {i + 1} 天</span>
            {formatDay(d)}
            <span className="block t-micro font-normal text-muted">{counts[d] ?? 0} 個活動</span>
          </Tab>
        ))}
      </TabList>
      {children}
    </Tabs.Root>
  );
}

/** 7:00～22:00 一小時一格；空格點了新增，有活動的格子顯示活動卡。 */
export function Timeline({
  activities,
  selectedId,
  onSelect,
  onAdd,
  onEdit,
  onRemove,
}: {
  /** 這一天的活動。 */
  activities: ActivityOut[];
  selectedId: string | null;
  onSelect: (activity: ActivityOut) => void;
  onAdd: (hour: number) => void;
  onEdit: (activity: ActivityOut) => void;
  onRemove: (activity: ActivityOut) => void;
}) {
  const byHour = new Map(activities.map((a) => [a.hour, a]));
  return (
    <ol className="m-0 mt-4 flex list-none flex-col p-0" data-testid="timeline">
      {HOURS.map((hour) => {
        const activity = byHour.get(hour);
        return (
          <li
            key={hour}
            data-testid={`slot-${hour}`}
            className="grid grid-cols-[44px_1fr] items-start gap-2 py-1 md:grid-cols-[72px_1fr] md:gap-3"
          >
            <span className="pt-2.5 text-right t-caption text-muted">{formatHour(hour)}</span>
            {activity ? (
              <ActivityCard
                activity={activity}
                selected={activity.id === selectedId}
                onSelect={onSelect}
                onEdit={onEdit}
                onRemove={onRemove}
              />
            ) : (
              <Button
                onClick={() => onAdd(hour)}
                className="w-full rounded-btn border border-dashed border-transparent bg-transparent px-3 py-2 text-left t-caption text-muted hover:border-line hover:bg-white hover:text-brand focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf"
              >
                ＋ 空檔，點這裡新增
              </Button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function ActivityCard({
  activity,
  selected,
  onSelect,
  onEdit,
  onRemove,
}: {
  activity: ActivityOut;
  selected: boolean;
  onSelect: (activity: ActivityOut) => void;
  onEdit: (activity: ActivityOut) => void;
  onRemove: (activity: ActivityOut) => void;
}) {
  const { label, Icon } = CATEGORIES[activity.category];
  const hasCoords = activity.latitude !== null && activity.longitude !== null;
  return (
    <article
      data-testid="activity-card"
      data-activity-id={activity.id}
      data-selected={selected}
      className={`flex items-start gap-1 rounded-tile bg-white shadow-card ${
        selected ? "border-2 border-brand bg-[#fcfef8]" : "border border-line"
      }`}
    >
      {/* 卡片本體是一顆按鈕：點了選取（時間軸、地圖、清單同步標示）；編輯與移除在旁邊，不巢狀 */}
      <Button
        onClick={() => onSelect(activity)}
        aria-pressed={selected}
        aria-label={`選取 ${activity.title}`}
        className="flex min-w-0 flex-1 items-start gap-3 rounded-tile p-3 text-left focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf md:px-4 md:py-3.5"
      >
        <span className="hidden size-10 flex-none place-items-center rounded-btn bg-leaf-soft text-brand md:grid">
          <Icon className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className={`${tag} bg-leaf-soft text-leaf-text`}>{label}</span>
            {!hasCoords && <span className={`${tag} bg-warn-soft text-warn-ink`}>沒有座標</span>}
          </span>
          <span className="mt-0.5 block t-label text-heading">{activity.title}</span>
          {activity.description && (
            <span className="mt-0.5 block t-caption text-ink">{activity.description}</span>
          )}
          <span className="mt-1 flex flex-wrap gap-x-3.5 t-caption text-muted">
            {activity.location && (
              <span className="inline-flex items-center gap-1">
                <PinIcon className="size-3.5" />
                {activity.location}
              </span>
            )}
            <span className="inline-flex items-center gap-1">
              <ClockIcon className="size-3.5" />
              {activity.duration} 小時
            </span>
          </span>
        </span>
      </Button>
      <Button
        className={`${iconBtn} mt-2.5`}
        aria-label={`編輯 ${activity.title}`}
        onClick={() => onEdit(activity)}
      >
        <PencilIcon className="size-4" />
      </Button>
      <Button
        className={`${iconBtn} mt-2.5 mr-2`}
        aria-label={`移除 ${activity.title}`}
        onClick={() => onRemove(activity)}
      >
        <CloseIcon className="size-4" />
      </Button>
    </article>
  );
}
