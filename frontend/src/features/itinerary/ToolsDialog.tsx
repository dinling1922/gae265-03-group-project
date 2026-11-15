import { Button } from "@base-ui/react/button";
import { btn, btnSm } from "../../components/ui/buttons";
import { Dialog, DialogShell } from "../../components/ui/DialogShell";
import { useToast } from "../../components/ui/Toast";
import type { ToolName } from "./webmcpSchemas";

/** 工具面板的說明（中文、給人看的）；agent 讀的說明在 webmcpSchemas。readOnly：只讀取不改資料。 */
const TOOLS: { name: ToolName; readOnly: boolean; text: string }[] = [
  {
    name: "get_trip_state",
    readOnly: true,
    text: "讀取目的地、城市、日期、每天的活動數與目前選取的活動。",
  },
  { name: "get_day_plan", readOnly: true, text: "讀取某一天的活動、空著的時段與地圖編號。" },
  {
    name: "set_trip_details",
    readOnly: false,
    text: "設定目的地、城市與日期；縮短日期會先回報要移除的活動，經使用者同意才執行。",
  },
  {
    name: "add_itinerary_activities",
    readOnly: false,
    text: "在空檔加入一或多個活動；時段已有活動的那筆會失敗並說明原因。",
  },
  {
    name: "update_itinerary_activity",
    readOnly: false,
    text: "修改一個活動的內容、座標，或移到其他日子與時段。",
  },
  { name: "remove_itinerary_activity", readOnly: false, text: "移除一個活動，讓時段空出來。" },
  { name: "set_map_view", readOnly: false, text: "切換地圖顯示某一天或整趟，並標示某個活動。" },
];

const PROMPTS = [
  "幫我排京都 9/18–9/22 五天的日程，喜歡咖啡和在地小吃",
  "第一天九點幫我找一間好吃的早餐店",
  "第二天下午可能下雨，換成室內的行程",
  "把地圖切到第三天，標出嵐山",
];

/** 這一頁開放給瀏覽器 agent 的 WebMCP 工具：說明、可複製的範例說法、目前瀏覽器是否支援。 */
export function ToolsDialog({
  open,
  onOpenChange,
  supported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  supported: boolean;
}) {
  const toast = useToast();
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.show("已複製");
    } catch {
      toast.show("複製失敗，請自己選取文字");
    }
  };
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      title="這一頁開放給 agent 的工具"
      description="瀏覽器裡的 agent（例如 WebMCP extension）可以讀取日程、在空檔加活動、修改或移除活動，並移動地圖。"
      variant="wide"
      testId="tools-dialog"
      actions={<Dialog.Close className={btn}>關閉</Dialog.Close>}
    >
      <section>
        <h3 className="m-0 mb-2 t-label text-heading">試試看這些說法</h3>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {PROMPTS.map((p) => (
            <li
              key={p}
              className="flex items-center justify-between gap-3 rounded-btn bg-leaf-soft px-3.5 py-2.5 t-body"
            >
              <span>「{p}」</span>
              <Button className={btnSm} onClick={() => void copy(p)} aria-label={`複製：${p}`}>
                複製
              </Button>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="m-0 mb-2 t-label text-heading">工具</h3>
        <ul className="m-0 flex list-none flex-col gap-2 p-0" data-testid="tools-list">
          {TOOLS.map((t) => (
            <li key={t.name} className="rounded-btn border border-line px-3.5 py-2.5">
              <code className="t-caption-bold text-brand">{t.name}</code>
              {t.readOnly && (
                <span className="ml-2 rounded-[6px] bg-leaf-soft px-2 t-micro leading-5 text-leaf-text">
                  唯讀
                </span>
              )}
              <p className="m-0 t-caption text-ink">{t.text}</p>
            </li>
          ))}
        </ul>
      </section>
      <p
        data-testid="webmcp-status"
        data-supported={supported}
        className="m-0 flex items-start gap-2 t-caption text-muted"
      >
        <span
          aria-hidden="true"
          className={`mt-1.5 size-2 flex-none rounded-full ${supported ? "bg-leaf" : "bg-sun"}`}
        />
        {supported
          ? "這個瀏覽器已啟用 WebMCP，agent 可以直接使用上面的工具。"
          : "這個瀏覽器還沒有啟用 WebMCP，agent 看不到這些工具。請用 Chrome 開啟 chrome://flags/#enable-webmcp-testing，設為 Enabled 後重新載入這一頁。"}
      </p>
    </DialogShell>
  );
}
