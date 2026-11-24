import type { ReactNode } from "react";
import { TermButton } from "../../components/ui/TermButton";

export type CompareColumn = { label: string; head: ReactNode };
export type CompareRow = {
  key: string;
  label: string;
  /** 你在意的順序（1 起算）；不是你在意的列不給。 */
  concernOrder?: number;
  onTerm: () => void;
  cells: [ReactNode, ReactNode];
};

/**
 * 保障比較表（DESIGN.md 6.15）：桌機三欄（項目｜推薦｜次推薦），推薦欄整欄品牌色框；
 * 你在意的列只用 leaf-soft 底標示（不加文字標籤）。手機：列名獨佔一列，下面推薦、次推薦兩欄並排。
 * 格子內容由呼叫端決定（保障項目文字照後端給的）。
 */
export function ComparisonTable({
  columns,
  rows,
  headLabel = "保障項目",
}: {
  headLabel?: string;
  columns: [CompareColumn, CompareColumn];
  rows: CompareRow[];
}) {
  // 推薦欄整欄品牌色框：表頭上緣、每格左右、最後一列下緣連成一個框（手機也一樣）
  const frame = "border-x-2 border-brand";
  return (
    <table className="w-full border-separate border-spacing-0 text-left max-md:block">
      <thead className="max-md:block">
        <tr className="max-md:grid max-md:grid-cols-2">
          <th
            scope="col"
            className="p-3 text-center align-bottom t-label text-heading max-md:col-span-2 max-md:pt-1"
          >
            {headLabel}
          </th>
          {columns.map((c, i) => (
            <th
              key={c.label}
              scope="col"
              className={`p-3 text-center align-bottom t-label text-heading ${i === 0 ? `${frame} rounded-t-tag border-t-2` : ""}`}
            >
              {c.head}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="max-md:block">
        {rows.map((row, r) => {
          const last = r === rows.length - 1;
          return (
            <tr
              key={row.key}
              className={`align-top max-md:grid max-md:grid-cols-2 ${
                row.concernOrder ? "bg-leaf-soft" : ""
              }`}
            >
              <th
                scope="row"
                className="border-t border-line p-3 font-normal max-md:col-span-2 max-md:pb-1"
              >
                <TermButton onClick={row.onTerm}>{row.label}</TermButton>
              </th>
              {row.cells.map((cell, i) => (
                <td
                  key={columns[i]?.label}
                  className={`p-3 t-body text-ink ${
                    i === 0
                      ? // 推薦欄：框線全是品牌色（不和 border-line 疊 class）；列與列的細線用內縮陰影畫
                        `${frame} ${last ? "rounded-b-tag border-b-2" : ""} md:shadow-[inset_0_1px_0_var(--color-line)]`
                      : "md:border-t md:border-line"
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
