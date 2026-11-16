import { Button } from "@base-ui/react/button";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { suggestConcerns } from "../client";
import { updateComparisonMutation } from "../client/@tanstack/react-query.gen";
import type { ComparisonOut, Concern, Suggestion } from "../client/types.gen";
import { ActionBar } from "../components/ui/ActionBar";
import { btn, btnPri } from "../components/ui/buttons";
import { FieldError } from "../components/ui/Field";
import { LoadingOverlay } from "../components/ui/LoadingOverlay";
import { PageHead } from "../components/ui/PageHead";
import { ConcernCard } from "../features/compare/ConcernCard";
import { WithComparison } from "../features/compare/WithComparison";
import { card } from "../features/incident/shared";

export const Route = createFileRoute("/_authed/compare/$id/concerns")({
  component: ConcernsPage,
});

// 畫面順序；satisfies 讓產生的 Concern 型別多或少一個時編譯失敗
const CONCERNS = Object.keys({
  突然生病就醫: 0,
  需要緊急送醫或家屬趕來: 0,
  行程取消或更改: 0,
  班機延誤: 0,
  行李延誤: 0,
  行李遺失或損壞: 0,
  證件遺失: 0,
  信用卡盜刷: 0,
  不小心弄傷別人或弄壞東西: 0,
} satisfies Record<Concern, 0>) as Concern[];

const MAX_CONCERNS = 3;

/** 1-2 選擇最在意的點：進來時讀回，上一步／下一步都先存檔再換頁。 */
function ConcernsPage() {
  const { id } = Route.useParams();
  return <WithComparison id={id}>{(c) => <ConcernsForm initial={c} />}</WithComparison>;
}

function ConcernsForm({ initial }: { initial: ComparisonOut }) {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const [concerns, setConcerns] = useState(initial.concerns);
  const suggestions = useSuggestions(id);
  // 推薦回來時只預先勾選一次，而且只在使用者還沒選任何一項時（spec #427）
  const prefilled = useRef(false);
  useEffect(() => {
    const data = suggestions.data;
    if (!data || prefilled.current) return;
    prefilled.current = true;
    if (data.length === MAX_CONCERNS) {
      setConcerns((current) => (current.length === 0 ? data.map((s) => s.concern) : current));
    }
  }, [suggestions.data]);
  const update = useMutation(updateComparisonMutation());
  const [goingNext, setGoingNext] = useState(false);

  const save = (to: "/compare/$id/itinerary" | "/compare/$id/result") => {
    setGoingNext(to === "/compare/$id/result");
    update.mutate(
      { path: { comparison_id: id }, body: { concerns } },
      { onSuccess: () => void navigate({ to, params: { id } }) },
    );
  };

  const toggle = (c: Concern) =>
    setConcerns(concerns.includes(c) ? concerns.filter((x) => x !== c) : [...concerns, c]);

  return (
    <main data-page-paper className="mx-auto flex max-w-[1040px] flex-col gap-6 font-ui md:px-6">
      <PageHead crumb="出發前：幫我選方案 · 2／3" title="選擇最在意的點" />
      <div className={`${card} flex flex-col gap-5`}>
        <h2 className="m-0 t-label text-heading">
          這趟旅行你最擔心什麼？依重要程度點選，最多 3 項。
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4">
          {CONCERNS.map((c) => {
            const order = concerns.indexOf(c) + 1;
            return (
              <ConcernCard
                key={c}
                concern={c}
                order={order}
                disabled={order === 0 && concerns.length >= MAX_CONCERNS}
                onClick={() => toggle(c)}
              />
            );
          })}
        </div>
        {suggestions.isPending ? (
          <SuggestionsSkeleton />
        ) : (
          suggestions.data &&
          suggestions.data.length > 0 && <SuggestionList items={suggestions.data} />
        )}
        {update.error != null && <FieldError>存檔失敗，請再試一次。</FieldError>}
      </div>
      <ActionBar twoColumns>
        <Button
          onClick={() => save("/compare/$id/itinerary")}
          disabled={update.isPending}
          className={btn}
        >
          上一步
        </Button>
        <ActionBar.Push>
          <ActionBar.Full>
            <Button
              onClick={() => save("/compare/$id/result")}
              disabled={concerns.length === 0 || update.isPending}
              className={btnPri}
            >
              下一步
            </Button>
          </ActionBar.Full>
        </ActionBar.Push>
      </ActionBar>
      {(update.isPending || update.isSuccess) && goingNext && (
        <LoadingOverlay>正在比較方案…</LoadingOverlay>
      )}
    </main>
  );
}

/** 依旅遊日程推薦的在意事項；每次進 1-2 都問後端（後端有存就不會再呼叫 LLM）。失敗就當作沒有推薦。 */
function useSuggestions(id: string) {
  return useQuery({
    queryKey: ["concernSuggestions", id],
    queryFn: async ({ signal }) =>
      (await suggestConcerns({ path: { comparison_id: id }, signal, throwOnError: true })).data,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

function SuggestionList({ items }: { items: Suggestion[] }) {
  return (
    <section data-testid="concern-suggestions" className="flex flex-col gap-2">
      <h3 className="m-0 t-label text-heading">依你的日程推薦</h3>
      <ul className="m-0 flex list-none flex-col gap-1 p-0 t-body text-ink">
        {items.map((s) => (
          <li key={s.concern}>
            <span className="font-semibold">{s.concern}</span>：{s.reason}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SuggestionsSkeleton() {
  return (
    <div
      data-testid="concern-suggestions-loading"
      aria-label="正在依日程推薦在意事項"
      role="status"
      className="flex animate-pulse flex-col gap-2"
    >
      <div className="h-5 w-32 rounded bg-line" />
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-5 w-3/4 rounded bg-line" />
      ))}
    </div>
  );
}
