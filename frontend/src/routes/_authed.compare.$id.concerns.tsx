import { Button } from "@base-ui/react/button";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { type CSSProperties, useEffect, useRef, useState } from "react";
import { suggestConcerns } from "../client";
import {
  listItineraryActivitiesOptions,
  updateComparisonMutation,
} from "../client/@tanstack/react-query.gen";
import type { ComparisonOut, Concern } from "../client/types.gen";
import { ActionBar } from "../components/ui/ActionBar";
import { btn, btnPri } from "../components/ui/buttons";
import { FieldError } from "../components/ui/Field";
import { LoadingOverlay } from "../components/ui/LoadingOverlay";
import { PageHead } from "../components/ui/PageHead";
import { ConcernCard } from "../features/compare/ConcernCard";
import {
  revealDelays,
  SuggestionTimeline,
  suggestionRows,
  useRevealPhase,
} from "../features/compare/SuggestionTimeline";
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

/** 1-2 推薦保障重點：進來時讀回，上一步／下一步都先存檔再換頁。 */
function ConcernsPage() {
  const { id } = Route.useParams();
  return <WithComparison id={id}>{(c) => <ConcernsForm initial={c} />}</WithComparison>;
}

function ConcernsForm({ initial }: { initial: ComparisonOut }) {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  // 進來時捲過網站 header，讓 9 個選項和下方的日程掃描動畫同時在畫面裡
  const mainRef = useRef<HTMLElement>(null);
  // 用區塊包起來：新版 Chrome 的 scrollIntoView 回傳 Promise，直接回傳會被 React 當成 cleanup 呼叫
  useEffect(() => {
    mainRef.current?.scrollIntoView({ block: "start" });
  }, []);
  const [concerns, setConcerns] = useState(initial.concerns);
  const suggestions = useSuggestions(id);
  const activities = useQuery(listItineraryActivitiesOptions({ path: { comparison_id: id } }));
  const rows = suggestionRows(
    initial.depart_date,
    initial.return_date,
    activities.data ?? [],
    suggestions.data ?? [],
  );
  const reveal = useRevealPhase(!suggestions.isPending, rows.filter((r) => r.items.length).length);
  const delays = revealDelays(rows);
  // 推薦揭曉時只預先勾選一次，而且只在使用者還沒選任何一項時（spec #427）
  const prefilled = useRef(false);
  const [popping, setPopping] = useState(false);
  useEffect(() => {
    const data = suggestions.data;
    if (!data || reveal.phase === "loading" || prefilled.current) return;
    prefilled.current = true;
    if (data.length === MAX_CONCERNS && concerns.length === 0) {
      setConcerns(data.map((s) => s.concern));
      setPopping(reveal.phase === "revealing");
    }
  }, [suggestions.data, reveal.phase, concerns]);
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
    <main
      ref={mainRef}
      data-page-paper
      className="mx-auto flex max-w-[1040px] flex-col gap-6 font-ui md:px-6"
    >
      <PageHead crumb="出發前：幫我選方案 · 2／3" title="推薦保障重點" />
      <div className={`${card} flex flex-col gap-5`}>
        <h2 className="m-0 t-label text-heading">
          依你的行程，建議優先注意這幾項。你可以再調整，最多 3 項。
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4">
          {CONCERNS.map((c) => {
            const order = concerns.indexOf(c) + 1;
            const pop = popping && reveal.phase === "revealing" && order > 0 && delays.has(c);
            return (
              <ConcernCard
                key={c}
                concern={c}
                order={order}
                disabled={order === 0 && concerns.length >= MAX_CONCERNS}
                onClick={() => toggle(c)}
                className={pop ? "sg-prefill" : ""}
                style={{ "--d": `${delays.get(c) ?? 0}ms` } as CSSProperties}
              />
            );
          })}
        </div>
        {(suggestions.isPending || (suggestions.data?.length ?? 0) > 0) && (
          <SuggestionTimeline
            {...reveal}
            rows={rows}
            selected={concerns}
            full={concerns.length >= MAX_CONCERNS}
            onToggle={toggle}
          />
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
    // 日程改了（例如載入範例日程）再回來，不能拿上次的快取預先勾選或顯示：離開頁面就丟掉
    gcTime: 0,
  });
}
