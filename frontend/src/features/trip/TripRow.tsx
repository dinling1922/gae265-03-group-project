import { Link } from "@tanstack/react-router";
import type { TripSummaryOut } from "../../client/types.gen";
import { ChevronIcon, ShieldIcon } from "../../components/icons";

const CLOSED_AS: Record<NonNullable<TripSummaryOut["closed_as"]>, string> = {
  sent: "已寄出",
  drafted: "已建草稿",
  self_submitted: "自行送件",
  closed: "已結案",
};

/** 行程名稱（規格 0-4）：目的地＋保險期間，例「日本 2026/07/05～07/10」；沒有目的地用保單名稱。 */
export function tripTitle(t: TripSummaryOut): string {
  const name = t.destination || t.display_name;
  if (!t.start_date) return name;
  const start = t.start_date.replaceAll("-", "/");
  const end =
    t.end_date && t.end_date !== t.start_date ? `～${t.end_date.slice(5).replace("-", "/")}` : "";
  return `${name} ${start}${end}`;
}

/** 第二行（規格 0-4）：事故類型以頓號串接＋完成度＋送件狀態；尚未產生總結一律「描述中」。 */
export function tripSubtitle(t: TripSummaryOut): string {
  if (t.state === "describing") return "描述中";
  const progress =
    t.state === "no_todos"
      ? "無待辦"
      : t.state === "done"
        ? "待辦已完成"
        : `待辦 ${t.todo_done}／${t.todo_total}`;
  return [t.incident_type_names.join("、"), progress, t.closed_as ? CLOSED_AS[t.closed_as] : null]
    .filter(Boolean)
    .join(" · ");
}

const ROW_CLASS =
  "group flex items-center gap-3 border-b border-line py-4 last:border-b-0 md:gap-6 md:py-[22px] md:pr-2";

/** 首頁「我的行程」與全部行程頁的一列：整列連到行程頁；有未完成待辦且未結案時右側顯示紅點（0-5a）。 */
export function TripRow({ trip: t }: { trip: TripSummaryOut }) {
  const body = (
    <>
      <span className="grid size-10 flex-none place-items-center rounded-full bg-leaf-soft text-leaf md:size-[52px]">
        <ShieldIcon className="size-[22px] md:size-7" />
      </span>
      <span className="min-w-0 flex-1">
        <span
          data-testid="trip-row-title"
          className="block truncate text-sm font-bold leading-[22px] text-ink md:text-lg md:leading-7"
        >
          {tripTitle(t)}
        </span>
        <span
          data-testid="trip-row-subtitle"
          className="mt-1 block truncate text-sm leading-[22px] text-ink md:mt-1.5 md:text-base md:leading-[26px]"
        >
          {tripSubtitle(t)}
        </span>
      </span>
      {t.has_unfinished && (
        <span
          role="img"
          aria-label="有未完成的待辦"
          data-testid="trip-row-dot"
          className="size-2.5 flex-none rounded-full bg-danger"
        />
      )}
      <span className="grid size-[26px] flex-none place-items-center rounded-full bg-leaf-soft text-leaf transition-colors group-hover:bg-leaf group-hover:text-white md:size-[30px]">
        <ChevronIcon className="size-4" />
      </span>
    </>
  );
  // 描述中、還有沒確認的事件（#345）：回 2-2 接續它（再跳確認彈窗）；行程頁還沒有東西可看
  if (t.state === "describing" && t.pending_incident_id) {
    return (
      <Link
        to="/incident/$id"
        params={{ id: t.pending_incident_id }}
        data-testid="trip-row"
        className={ROW_CLASS}
      >
        {body}
      </Link>
    );
  }
  return (
    <Link
      to="/trip/$policyId"
      params={{ policyId: t.policy_id }}
      data-testid="trip-row"
      className={ROW_CLASS}
    >
      {body}
    </Link>
  );
}
