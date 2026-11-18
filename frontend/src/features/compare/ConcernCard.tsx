import { Button } from "@base-ui/react/button";
import type { ComponentType, CSSProperties } from "react";
import type { Concern } from "../../client/types.gen";
import {
  BagIcon,
  BagWarnIcon,
  CardIcon,
  ClockIcon,
  IdIcon,
  MedIcon,
  PulseIcon,
  WarnIcon,
  XCalIcon,
} from "../../components/icons";

const ICONS: Record<Concern, ComponentType<{ className?: string }>> = {
  突然生病就醫: MedIcon,
  需要緊急送醫或家屬趕來: PulseIcon,
  行程取消或更改: XCalIcon,
  班機延誤: ClockIcon,
  行李延誤: BagIcon,
  行李遺失或損壞: BagWarnIcon,
  證件遺失: IdIcon,
  信用卡盜刷: CardIcon,
  不小心弄傷別人或弄壞東西: WarnIcon,
};

export function ConcernIcon({ concern, className }: { concern: Concern; className?: string }) {
  const Icon = ICONS[concern];
  return <Icon className={className} />;
}

/**
 * 在意事項卡（DESIGN.md 6.8）：圖示加名稱。選中是 leaf-soft 底加品牌色框、右上角順序圓；
 * `order` 是點選順序（1 起算，0＝沒選）；選滿 3 項時其餘 `disabled`。
 */
export function ConcernCard({
  concern,
  order,
  disabled,
  onClick,
  className = "",
  style,
}: {
  concern: Concern;
  order: number;
  disabled?: boolean;
  onClick: () => void;
  /** 給動畫用（1-2 推薦的閃爍與預先勾選） */
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <Button
      type="button"
      aria-pressed={order > 0}
      disabled={disabled}
      onClick={onClick}
      style={style}
      className={`${className} relative flex min-h-[88px] items-center gap-3 rounded-chip border-2 border-line bg-white py-3 pr-9 pl-4 text-left t-caption-bold text-ink hover:border-leaf focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf aria-pressed:border-brand aria-pressed:bg-leaf-soft disabled:cursor-not-allowed disabled:opacity-40 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40`}
    >
      <ConcernIcon concern={concern} className="size-6 flex-none text-leaf" />
      <span>{concern}</span>
      {order > 0 && (
        <>
          <span className="sr-only"> 第 {order} 順位</span>
          <span
            aria-hidden="true"
            data-order-badge
            className="absolute top-2 right-2 grid size-5 place-items-center rounded-full bg-brand t-micro text-white"
          >
            {order}
          </span>
        </>
      )}
    </Button>
  );
}
