import { useSyncExternalStore } from "react";

/**
 * 訂閱 CSS media query 的結果（視窗改變、手機轉向時會更新）。
 * 沒有 matchMedia 的環境（測試、SSR）一律回 false。
 * 需要「只在某種視窗才換一個元件」時用；單純改樣式用 Tailwind 的 variant。
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia?.(query);
      mql?.addEventListener("change", onChange);
      return () => mql?.removeEventListener("change", onChange);
    },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}

/** 橫式的矮視窗（手機橫著拿；鍵盤出現前高度約 420px）。 */
export const SHORT_LANDSCAPE_QUERY = "(orientation: landscape) and (max-height: 500px)";
