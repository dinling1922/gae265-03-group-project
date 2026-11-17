import { Button } from "@base-ui/react/button";
import { Menu } from "@base-ui/react/menu";
import type { Destination } from "../../client/types.gen";
import { SparkleIcon } from "../../components/icons";
import { btnPri, btnSm, btnSmDanger } from "../../components/ui/buttons";
import { Field, FieldError, FieldHint, fieldInput, fieldSelect } from "../../components/ui/Field";
import { PageHead } from "../../components/ui/PageHead";
import { DaysSummary } from "../compare/DaysSummary";
import { DEMO_ITINERARIES, type DemoItinerary } from "./demoItineraries";
import { addDays, DESTINATIONS, today, tripDays } from "./tripRules";

export type TripForm = {
  destination: string;
  city: string;
  depart: string;
  returnDate: string;
};

/** 左欄：旅行規劃表單（目的地、城市、日期）、邀請 agent 的卡片、下一步。每次改動由頁面即時存檔。 */
export function TripSidebar({
  form,
  onChange,
  errors,
  saveError,
  onOpenTools,
  activityCount,
  onClear,
  onLoadDemo,
  demoLoading,
  nextDisabled,
  onNext,
}: {
  form: TripForm;
  onChange: (patch: Partial<TripForm>) => void;
  errors: string[];
  saveError: string | null;
  /** 開啟 WebMCP 工具面板；還沒有面板時不給，按鈕停用。 */
  onOpenTools?: () => void;
  /** 目前排了幾個活動；大於 0 才顯示「清除所有活動」。 */
  activityCount: number;
  onClear: () => void;
  /** 選了一份範例日程（GLOSSARY「範例日程」）。 */
  onLoadDemo: (demo: DemoItinerary) => void;
  demoLoading: boolean;
  nextDisabled: boolean;
  onNext: () => void;
}) {
  const { destination, city, depart, returnDate } = form;
  return (
    <aside className="flex flex-col gap-5 border-line p-4 md:sticky md:top-4 min-[768px]:row-span-2 md:max-h-[calc(100vh-2rem)] md:self-start md:overflow-auto md:border-r md:p-6 min-[860px]:row-span-1">
      <PageHead crumb="出發前：幫我選方案 · 1／3" title="規劃旅遊日程" />
      <p className="m-0 t-caption text-muted">填好目的地和日期，剩下的細節可以交給 agent。</p>
      <div className="flex flex-col gap-4">
        <Field label="目的地" htmlFor="destination">
          <select
            id="destination"
            value={destination}
            onChange={(e) => onChange({ destination: e.target.value })}
            className={`${fieldSelect} w-full`}
          >
            <option value="" disabled>
              請選擇
            </option>
            {DESTINATIONS.map((d: Destination) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={
            <>
              城市 <span className="font-normal text-muted">選填</span>
            </>
          }
          htmlFor="city"
        >
          <input
            id="city"
            value={city}
            maxLength={30}
            placeholder="例如：京都"
            onChange={(e) => onChange({ city: e.target.value })}
            className={`${fieldInput} w-full`}
          />
        </Field>
        <div className="flex flex-col gap-4">
          <Field label="出發日" htmlFor="depart">
            <input
              id="depart"
              type="date"
              value={depart}
              min={today()}
              onChange={(e) => onChange({ depart: e.target.value })}
              className={`${fieldInput} w-full`}
            />
          </Field>
          <Field label="回程日" htmlFor="return">
            <input
              id="return"
              type="date"
              value={returnDate}
              min={depart ? addDays(depart, 3) : undefined}
              max={depart ? addDays(depart, 9) : undefined}
              onChange={(e) => onChange({ returnDate: e.target.value })}
              aria-describedby="return-hint"
              className={`${fieldInput} w-full`}
            />
          </Field>
        </div>
        <FieldHint id="return-hint">旅遊天數最多 10 天（含頭尾）</FieldHint>
        <DaysSummary>
          {depart && returnDate && returnDate >= depart
            ? `共 ${tripDays(depart, returnDate)} 天`
            : "選好兩個日期後顯示天數"}
        </DaysSummary>
        <Button onClick={onNext} disabled={nextDisabled} className={`${btnPri} w-full`}>
          下一步：選擇在意的事
        </Button>
        {errors.length > 0 && (
          <ul role="alert" className="m-0 list-none space-y-1 p-0 t-caption-bold text-danger">
            {errors.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
        <FieldError>{saveError}</FieldError>
      </div>
      <Menu.Root>
        <Menu.Trigger disabled={demoLoading} data-testid="demo-menu" className={`${btnSm} w-full`}>
          {demoLoading ? "載入中…" : "載入範例日程 ▾"}
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner sideOffset={6} className="z-50">
            <Menu.Popup className="flex min-w-64 flex-col rounded-tile border border-line bg-white p-1.5 shadow-card">
              {DEMO_ITINERARIES.map((demo) => (
                <Menu.Item
                  key={demo.id}
                  onClick={() => onLoadDemo(demo)}
                  data-testid={`demo-${demo.id}`}
                  className="cursor-pointer rounded-tag px-3 py-2 t-body text-ink data-[highlighted]:bg-leaf-soft"
                >
                  {demo.label}
                </Menu.Item>
              ))}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
      {activityCount > 0 && (
        <Button
          onClick={onClear}
          data-testid="clear-activities"
          className={`${btnSmDanger} w-full`}
        >
          清除所有活動（{activityCount}）
        </Button>
      )}
      <Button
        onClick={onOpenTools}
        disabled={!onOpenTools}
        data-testid="open-tools"
        className="flex w-full items-center gap-3 rounded-tile border border-[#dce9c4] bg-leaf-soft p-3.5 text-left hover:bg-[#e8f2d6] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf disabled:opacity-40"
      >
        <span className="grid size-9 flex-none place-items-center rounded-tag bg-white text-leaf-text">
          <SparkleIcon className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <b className="t-label text-heading">邀請你的 agent</b>
          <span className="m-0 block t-caption text-muted">
            它可以排活動、填空檔，也能幫你改目的地和日期。
          </span>
          <span className="mt-1.5 flex items-center gap-2 t-caption-bold text-heading">
            <span
              aria-hidden="true"
              className="size-2 rounded-full bg-leaf shadow-[0_0_0_3px_var(--color-leaf-soft)]"
            />
            WebMCP · 8 個工具
          </span>
        </span>
        <span aria-hidden="true" className="t-label text-brand">
          →
        </span>
      </Button>
    </aside>
  );
}
