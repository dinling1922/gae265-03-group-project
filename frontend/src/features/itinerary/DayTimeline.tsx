import { Button } from "@base-ui/react/button";
import type { ReactNode } from "react";
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
  return (
    <Tabs.Root value={value} onValueChange={(v) => onChange(String(v))}>
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
  onAdd,
  onEdit,
  onRemove,
}: {
  /** 這一天的活動。 */
  activities: ActivityOut[];
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
              <ActivityCard activity={activity} onEdit={onEdit} onRemove={onRemove} />
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
  onEdit,
  onRemove,
}: {
  activity: ActivityOut;
  onEdit: (activity: ActivityOut) => void;
  onRemove: (activity: ActivityOut) => void;
}) {
  const { label, Icon } = CATEGORIES[activity.category];
  const hasCoords = activity.latitude !== null && activity.longitude !== null;
  return (
    <article
      data-testid="activity-card"
      data-activity-id={activity.id}
      className="flex items-start gap-3 rounded-tile border border-line bg-white p-3 shadow-card md:px-4 md:py-3.5"
    >
      <span className="hidden size-10 flex-none place-items-center rounded-btn bg-leaf-soft text-brand md:grid">
        <Icon className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`${tag} bg-leaf-soft text-leaf-text`}>{label}</span>
          {!hasCoords && <span className={`${tag} bg-warn-soft text-warn-ink`}>沒有座標</span>}
        </div>
        <h3 className="m-0 mt-0.5 t-label text-heading">{activity.title}</h3>
        {activity.description && (
          <p className="m-0 mt-0.5 t-caption text-ink">{activity.description}</p>
        )}
        <p className="m-0 mt-1 flex flex-wrap gap-x-3.5 t-caption text-muted">
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
        </p>
      </div>
      <Button
        className={iconBtn}
        aria-label={`編輯 ${activity.title}`}
        onClick={() => onEdit(activity)}
      >
        <PencilIcon className="size-4" />
      </Button>
      <Button
        className={iconBtn}
        aria-label={`移除 ${activity.title}`}
        onClick={() => onRemove(activity)}
      >
        <CloseIcon className="size-4" />
      </Button>
    </article>
  );
}
