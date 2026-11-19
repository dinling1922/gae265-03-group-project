import { useState } from "react";
import type {
  BenefitAmountOut,
  ComparisonOut,
  ComparisonTableRowOut,
  RecommendationOut,
} from "../../client/types.gen";
import { money } from "../../lib/format";
import { type CompareColumn, type CompareRow, ComparisonTable } from "./ComparisonTable";
import { TermDialog } from "./TermDialog";

/** 1-3 比較表與 1-4 你在意的保障共用：欄位表頭、列（cells、你在意順序）、名詞解釋內容。 */
export const CARD_LABEL = { recommended: "推薦", alternative: "次推薦" } as const;

export function Benefits({ items }: { items: BenefitAmountOut[] }) {
  if (items.length === 0) return <span className="text-muted">未列</span>;
  return (
    <ul className="m-0 list-none space-y-1 p-0">
      {items.map((b) => (
        <li key={b.name}>
          {b.name}：{money(b.amount)}
        </li>
      ))}
    </ul>
  );
}

export const compareColumns = (rec: RecommendationOut) =>
  (["recommended", "alternative"] as const).map((c) => ({
    label: CARD_LABEL[c],
    head: (
      <>
        <span className="block t-micro text-leaf-text">{CARD_LABEL[c]}</span>
        <span className="block t-caption text-muted">{rec[c].company}</span>
        {rec[c].name}
      </>
    ),
  })) as [CompareColumn, CompareColumn];

export const compareRows = (
  comparison: ComparisonOut,
  rows: ComparisonTableRowOut[],
  onTerm: (row: ComparisonTableRowOut) => void,
): CompareRow[] =>
  rows.map((row) => ({
    key: row.key,
    label: row.label,
    concernOrder: row.concern
      ? comparison.concerns.indexOf(row.concern) + 1 || undefined
      : undefined,
    onTerm: () => onTerm(row),
    cells: [
      <Benefits key="r" items={row.recommended} />,
      <Benefits key="a" items={row.alternative} />,
    ],
  }));

export const rowTerm = (row: ComparisonTableRowOut, rec: RecommendationOut) => ({
  title: row.label,
  explanation: row.explanation,
  details: (["recommended", "alternative"] as const).map((which) => (
    <div key={which} className="flex flex-col gap-1">
      <p className="m-0 t-label text-heading">
        {CARD_LABEL[which]}｜{rec[which].name}
      </p>
      <Benefits items={row[which]} />
    </div>
  )),
});

/** 你在意的列（concern 在使用者選的清單內），照使用者選的順序。 */
export const concernRows = (comparison: ComparisonOut, rec: RecommendationOut) => {
  const order = (r: ComparisonTableRowOut) =>
    r.concern ? comparison.concerns.indexOf(r.concern) : -1;
  return rec.table.filter((r) => order(r) >= 0).sort((a, b) => order(a) - order(b));
};

/** 自帶名詞解釋視窗的比較表；rows 沒給就是你在意的列。 */
export function ConcernTable({
  comparison,
  rec,
  rows,
}: {
  comparison: ComparisonOut;
  rec: RecommendationOut;
  rows?: ComparisonTableRowOut[];
}) {
  const [open, setOpen] = useState<ComparisonTableRowOut | null>(null);
  return (
    <>
      <ComparisonTable
        columns={compareColumns(rec)}
        rows={compareRows(comparison, rows ?? concernRows(comparison, rec), setOpen)}
      />
      <TermDialog term={open && rowTerm(open, rec)} onClose={() => setOpen(null)} />
    </>
  );
}
