import type { ComponentType } from "react";
import type { ActivityCategory } from "../../client/types.gen";
import {
  BedIcon,
  CoffeeIcon,
  CompassIcon,
  EatIcon,
  LandmarkIcon,
  MoonIcon,
} from "../../components/icons";

// 宣告順序就是下拉選單的順序；satisfies 讓產生的 ActivityCategory 型別多或少一個時編譯失敗
export const CATEGORIES = {
  eat: { label: "美食", Icon: EatIcon },
  explore: { label: "探索", Icon: CompassIcon },
  culture: { label: "文化", Icon: LandmarkIcon },
  coffee: { label: "咖啡", Icon: CoffeeIcon },
  rest: { label: "休息", Icon: MoonIcon },
  stay: { label: "住宿", Icon: BedIcon },
} satisfies Record<
  ActivityCategory,
  { label: string; Icon: ComponentType<{ className?: string }> }
>;

export const CATEGORY_KEYS = Object.keys(CATEGORIES) as ActivityCategory[];
