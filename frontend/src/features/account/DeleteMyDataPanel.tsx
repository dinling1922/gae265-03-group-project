import { Button } from "@base-ui/react/button";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { deleteMyDataMutation } from "../../client/@tanstack/react-query.gen";
import { ShieldIcon } from "../../components/icons";
import { btnSm } from "../../components/ui/buttons";

/** 帳號頁的「刪除我的所有資料」（ADR-0009）：二次確認，說清楚刪什麼、不刪什麼。 */
export function DeleteMyDataPanel() {
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const remove = useMutation({
    ...deleteMyDataMutation(),
    onSuccess: async () => {
      // 畫面上快取的保單、事件、對話都已不存在
      queryClient.clear();
      await navigate({ to: "/" });
    },
  });

  return (
    <section
      aria-labelledby="delete-data-title"
      className="rounded-2xl border border-line/60 bg-white px-3 pt-4 pb-4 shadow-card md:rounded-3xl md:px-4 md:pt-5 md:pb-5"
      data-testid="delete-my-data"
    >
      <div className="flex items-center gap-2.5 border-b border-line pt-1 pb-3.5 pl-1 md:gap-3.5 md:pt-0 md:pb-[18px] md:pl-2">
        <ShieldIcon className="size-7 flex-none text-leaf md:size-9" />
        <h2
          id="delete-data-title"
          className="text-xl font-bold leading-[30px] text-heading md:text-2xl md:leading-9"
        >
          我的資料
        </h2>
      </div>
      <div className="space-y-4 px-1 pt-4 md:px-2">
        <p className="m-0 t-body text-ink">
          我們只保存你輸入的行程資訊與保單的整理結果，不保存保單與理賠文件的原檔。你可以隨時刪除全部資料。
        </p>
        {!confirming ? (
          <Button
            className={btnSm}
            onClick={() => setConfirming(true)}
            data-testid="delete-my-data-start"
          >
            刪除我的所有資料
          </Button>
        ) : (
          <div
            className="space-y-3 rounded-tile border border-line bg-paper p-4"
            data-testid="delete-my-data-confirm"
          >
            <p className="m-0 t-body text-ink">
              確定要刪除嗎？你的所有保單、行程與事件、待辦與文件紀錄、對話、方案比較都會被刪除，無法復原，
              同時也會取消 Google 授權。你的帳號會保留，之後仍可登入。已存進你 Google Drive 的文件與
              Gmail 裡的草稿不會被刪除，請自行到 Google 刪除。
            </p>
            <div className="flex gap-2">
              <Button
                className={btnSm}
                onClick={() => remove.mutate({})}
                disabled={remove.isPending}
                data-testid="delete-my-data-yes"
              >
                確定刪除
              </Button>
              <Button className={btnSm} onClick={() => setConfirming(false)}>
                保留
              </Button>
            </div>
          </div>
        )}
        {remove.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger">
            刪除失敗，請再試一次。
          </p>
        )}
      </div>
    </section>
  );
}
