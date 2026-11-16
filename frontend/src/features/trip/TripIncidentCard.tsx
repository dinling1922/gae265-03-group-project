import type { TripIncidentOut } from "../../client/types.gen";
import { btnSm } from "../../components/ui/buttons";
import { dateOf, IncidentCardBody } from "../incident/IncidentCard";
import { card } from "../incident/shared";

const TAG = "rounded-xl px-2.5 py-0.5 text-[13px] leading-5 font-bold";

/**
 * 行程頁的事故卡（規格 2-3a）：標題列＝事故類型與發生日期、標籤；沒選中的事故右側有「問答」與「▸」，
 * 選中的事故在手機版也有「問答」（桌機的右欄已經在顯示它）。
 * 選中的事故一定展開（右欄問答顯示它的對話）；其他預設收起，按「▸」展開。
 */
export function TripIncidentCard({
  incident,
  selected,
  expanded,
  locked,
  onAsk,
  onToggle,
}: {
  incident: TripIncidentOut;
  selected: boolean;
  expanded: boolean;
  /** 行程結案後沒有問答，只剩展開收起。 */
  locked: boolean;
  onAsk: () => void;
  onToggle: () => void;
}) {
  const date = dateOf(incident);
  const name = `${incident.incident_type_name ?? "事故"}${date ? ` · ${date}` : ""}`;
  const unsure = incident.clauses.filter((c) => c.conclusion === "insufficient").length;
  // 沒有任何可能適用的項目：有任何一項待確認是「資訊不足」（補充說明還有機會改變結果），全部不賠才是「不符保障」
  const noResult = !incident.covered;
  const insufficient = noResult && unsure > 0;
  const open = selected || expanded;
  const bodyId = `incident-${incident.id}`;
  return (
    <article
      className={`${card} flex scroll-mt-6 flex-col gap-3.5`}
      aria-label={`事故 ${name}`}
      data-testid="trip-incident"
      data-incident-id={incident.id}
      data-selected={selected || undefined}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="m-0 t-subsection-title text-heading" data-testid="incident-type">
          {name}
        </h3>
        {unsure > 0 && (
          <span data-testid="unsure-count" className={`${TAG} bg-warn-soft text-warn-ink`}>
            待確認 {unsure}
          </span>
        )}
        {noResult && !insufficient && (
          <span data-testid="not-covered" className={`${TAG} bg-paper text-muted`}>
            不符保障
          </span>
        )}
        {insufficient && (
          <span data-testid="insufficient" className={`${TAG} bg-warn-soft text-warn-ink`}>
            資訊不足，尚無法判斷
          </span>
        )}
        {!selected && (
          <span className="ml-auto flex items-center gap-2">
            {!locked && (
              <button type="button" className={btnSm} onClick={onAsk} data-testid="incident-ask">
                問答
              </button>
            )}
            <button
              type="button"
              aria-expanded={open}
              aria-controls={bodyId}
              aria-label={`${open ? "收起" : "展開"}${name}`}
              onClick={onToggle}
              data-testid="incident-toggle"
              className="grid size-9 place-items-center rounded-tag border border-line bg-white text-leaf-text hover:border-brand"
            >
              {open ? "▾" : "▸"}
            </button>
          </span>
        )}
        {selected && !locked && (
          // 桌機問答是固定右欄、已經在顯示選中的事故；手機面板預設收起，選中的事故也要有入口（#422）
          <button
            type="button"
            className={`${btnSm} ml-auto lg:hidden`}
            onClick={onAsk}
            data-testid="incident-ask-mobile"
          >
            問答
          </button>
        )}
      </div>
      {insufficient && (
        <p className="m-0 t-caption text-muted" data-testid="insufficient-note">
          目前的資訊還無法判斷能不能理賠，可以在問答補充說明。
        </p>
      )}
      <IncidentCardBody ctx={incident} id={bodyId} hidden={!open} />
    </article>
  );
}
