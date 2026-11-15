import { Button } from "@base-ui/react/button";
import { useState } from "react";
import type { ActivityCategory, ActivityOut } from "../../client/types.gen";
import { btn, btnPri } from "../../components/ui/buttons";
import { Dialog, DialogShell } from "../../components/ui/DialogShell";
import { Field, FieldError, FieldHint, fieldInput, fieldSelect } from "../../components/ui/Field";
import { CATEGORIES, CATEGORY_KEYS } from "./categories";
import { formatDay, formatHour } from "./tripRules";

export type ActivityForm = {
  title: string;
  location: string;
  description: string;
  category: ActivityCategory;
  duration: number;
};

/** 新增（帶日期與整點）或編輯（帶既有活動）；沒帶 target 時視窗關閉。 */
export type ActivityTarget =
  | { kind: "add"; date: string; hour: number }
  | { kind: "edit"; activity: ActivityOut };

const DURATIONS = [0.5, 1, 1.5, 2, 3, 4, 6, 8];

function hours(n: number): string {
  return `${n} 小時`;
}

/** 新增／編輯活動。座標不在這裡填：自己新增的活動沒有座標，請 agent 補上。 */
export function ActivityDialog({
  target,
  onClose,
  onSubmit,
}: {
  target: ActivityTarget | null;
  onClose: () => void;
  /** 回傳錯誤訊息表示失敗（視窗留著顯示），回傳 null 表示成功（視窗關閉）。 */
  onSubmit: (target: ActivityTarget, form: ActivityForm) => Promise<string | null>;
}) {
  return (
    <DialogShell
      open={target !== null}
      onOpenChange={(open) => !open && onClose()}
      title={target?.kind === "edit" ? "編輯活動" : "新增活動"}
      description={
        target && (
          <p className="m-0 t-caption text-muted">
            {target.kind === "add"
              ? `${formatDay(target.date)} ${formatHour(target.hour)}`
              : `${formatDay(target.activity.date)} ${formatHour(target.activity.hour)}`}
          </p>
        )
      }
      testId="activity-dialog"
    >
      {/* key：換一個活動或時段就重新初始化表單 */}
      {target && (
        <ActivityFormBody
          key={target.kind === "edit" ? target.activity.id : `${target.date}-${target.hour}`}
          target={target}
          onSubmit={onSubmit}
        />
      )}
    </DialogShell>
  );
}

function ActivityFormBody({
  target,
  onSubmit,
}: {
  target: ActivityTarget;
  onSubmit: (target: ActivityTarget, form: ActivityForm) => Promise<string | null>;
}) {
  const initial = target.kind === "edit" ? target.activity : null;
  const [title, setTitle] = useState(initial?.title ?? "");
  const [location, setLocation] = useState(initial?.location ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [category, setCategory] = useState<ActivityCategory>(initial?.category ?? "explore");
  const [duration, setDuration] = useState(initial?.duration ?? 1);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // agent 排的時長可能不在選項裡（例如 2.5），編輯時也要選得到
  const durations = DURATIONS.includes(duration)
    ? DURATIONS
    : [...DURATIONS, duration].sort((a, b) => a - b);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setPending(true);
        setError(null);
        const message = await onSubmit(target, {
          title: title.trim(),
          location: location.trim(),
          description: description.trim(),
          category,
          duration,
        });
        setPending(false);
        if (message) setError(message);
      }}
    >
      <Field label="要做什麼" htmlFor="activity-title">
        <input
          id="activity-title"
          value={title}
          maxLength={100}
          placeholder="例如：錦市場吃生魚片丼"
          onChange={(e) => setTitle(e.target.value)}
          className={`${fieldInput} w-full`}
        />
      </Field>
      <Field label="地點" htmlFor="activity-location">
        <input
          id="activity-location"
          value={location}
          maxLength={200}
          placeholder="店名、地區或地址"
          onChange={(e) => setLocation(e.target.value)}
          className={`${fieldInput} w-full`}
        />
      </Field>
      <Field label="備註" htmlFor="activity-description">
        <textarea
          id="activity-description"
          rows={3}
          value={description}
          maxLength={500}
          placeholder="想記下的細節"
          onChange={(e) => setDescription(e.target.value)}
          className={`${fieldInput} w-full`}
        />
      </Field>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="分類" htmlFor="activity-category">
          <select
            id="activity-category"
            value={category}
            onChange={(e) => setCategory(e.target.value as ActivityCategory)}
            className={`${fieldSelect} w-full`}
          >
            {CATEGORY_KEYS.map((k) => (
              <option key={k} value={k}>
                {CATEGORIES[k].label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="時長" htmlFor="activity-duration">
          <select
            id="activity-duration"
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            className={`${fieldSelect} w-full`}
          >
            {durations.map((d) => (
              <option key={d} value={d}>
                {hours(d)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <FieldHint>自己新增的活動沒有座標，不會出現在地圖上；可以請 agent 補上。</FieldHint>
      <FieldError>{error}</FieldError>
      <div className="flex flex-wrap justify-end gap-3">
        <Dialog.Close className={btn}>取消</Dialog.Close>
        <Button type="submit" disabled={!title.trim() || pending} className={btnPri}>
          儲存
        </Button>
      </div>
    </form>
  );
}
