import { Button } from "@base-ui/react/button";
import type { Destination } from "../../client/types.gen";
import { SparkleIcon } from "../../components/icons";
import { btnPri } from "../../components/ui/buttons";
import { Field, FieldError, FieldHint, fieldInput, fieldSelect } from "../../components/ui/Field";
import { PageHead } from "../../components/ui/PageHead";
import { DaysSummary } from "../compare/DaysSummary";
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
  nextDisabled,
  onNext,
}: {
  form: TripForm;
  onChange: (patch: Partial<TripForm>) => void;
  errors: string[];
  saveError: string | null;
  /** 開啟 WebMCP 工具面板；還沒有面板時不給，按鈕停用。 */
  onOpenTools?: () => void;
  nextDisabled: boolean;
  onNext: () => void;
}) {
  const { destination, city, depart, returnDate } = form;
  return (
    <aside className="flex flex-col gap-5 border-line p-4 md:sticky md:top-4 md:row-span-2 md:max-h-[calc(100vh-2rem)] md:self-start md:overflow-auto md:border-r md:p-6 min-[1091px]:row-span-1">
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
        {errors.length > 0 && (
          <ul role="alert" className="m-0 list-none space-y-1 p-0 t-caption-bold text-danger">
            {errors.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
        <FieldError>{saveError}</FieldError>
      </div>
      <div className="flex items-center gap-3 rounded-tile border border-[#dce9c4] bg-leaf-soft p-3.5">
        <span className="grid size-9 flex-none place-items-center rounded-tag bg-white text-leaf-text">
          <SparkleIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <b className="t-label text-heading">邀請你的 agent</b>
          <p className="m-0 t-caption text-muted">它可以排活動、填空檔，也能幫你改目的地和日期。</p>
        </div>
        <Button
          onClick={onOpenTools}
          disabled={!onOpenTools}
          aria-label="看 agent 可以用的工具"
          className="rounded-lg px-2 py-1 t-label text-brand hover:bg-white focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf disabled:opacity-40"
        >
          →
        </Button>
      </div>
      <Button onClick={onNext} disabled={nextDisabled} className={`${btnPri} w-full`}>
        下一步：選擇在意的事
      </Button>
    </aside>
  );
}
