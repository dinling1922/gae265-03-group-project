import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { createAssistantSayMutation } from "../../client/@tanstack/react-query.gen";
import type { SayLine } from "../../client/types.gen";
import { btn } from "../../components/ui/buttons";
import { Dialog } from "../../components/ui/DialogShell";

/** 一句話：當地語言（最大）、英文、中文小字三行並列；只顯示英文時沒有當地語言那一行。 */
function Say({ line, level = "body" }: { line: SayLine; level?: "lead" | "unit" | "body" }) {
  const big = level === "lead" ? "text-2xl" : level === "unit" ? "text-xl" : "text-lg";
  return (
    <div className="flex flex-col gap-1">
      {line.local && (
        <p lang="ja" className={`m-0 ${big} leading-snug font-bold text-heading`}>
          {line.local}
        </p>
      )}
      <p lang="en" className={`m-0 ${line.local ? "text-lg" : big} leading-snug text-ink`}>
        {line.english}
      </p>
      <p className="m-0 t-caption text-muted">{line.chinese}</p>
    </div>
  );
}

/**
 * 「小助理幫你說」（#568）：旅客把手機拿給櫃檯人員看，請對方開立理賠需要的文件。
 * 打開時才請後端翻譯（每次都重翻，還沒存），翻譯中顯示「正在翻譯…」。
 */
export function AssistantSayDialog({
  policyId,
  open,
  onOpenChange,
}: {
  policyId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { mutate, reset, data, isPending, isError } = useMutation(createAssistantSayMutation());
  useEffect(() => {
    if (open) mutate({ path: { policy_id: policyId } });
    else reset();
  }, [open, policyId, mutate, reset]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-scrim" />
        <Dialog.Popup
          data-testid="assistant-say"
          className="fixed bottom-0 left-0 z-40 flex max-h-[90dvh] w-full flex-col gap-4 overflow-auto rounded-t-panel bg-white p-5.5 shadow-dialog md:top-1/2 md:bottom-auto md:left-1/2 md:w-[min(45rem,calc(100vw-2rem))] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-panel md:p-7"
        >
          <div className="flex items-start gap-3">
            <Dialog.Title className="m-0 t-dialog-title text-heading">小助理幫你說</Dialog.Title>
            <Dialog.Close className={`${btn} ml-auto`} data-testid="assistant-say-close">
              關閉
            </Dialog.Close>
          </div>
          <Dialog.Description render={<p />} className="m-0 t-body text-ink">
            把手機拿給櫃檯人員看，對方就知道要開哪些文件。
          </Dialog.Description>

          {isPending && (
            <p role="status" className="m-0 py-6 text-xl text-heading">
              正在翻譯…
            </p>
          )}
          {isError && (
            <p role="alert" className="m-0 py-6 text-lg text-danger">
              翻譯失敗，請關閉後再試一次。
            </p>
          )}
          {data && (
            <div className="flex flex-col gap-6" data-testid="assistant-say-content">
              <Say line={data.request} level="lead" />
              {data.sections.map((section) => (
                <section
                  key={section.unit.chinese}
                  className="flex flex-col gap-4 border-t border-line pt-4"
                >
                  <h3 className="m-0 text-xl leading-snug font-bold text-heading">
                    {section.unit.local ? `${section.unit.local} / ` : ""}
                    {section.unit.english}（{section.unit.chinese}）
                  </h3>
                  {section.documents.map((doc) => (
                    <div key={doc.code} className="flex flex-col gap-3">
                      <Say line={doc.name} level="unit" />
                      {doc.items.length > 0 && (
                        <ul className="m-0 flex list-disc flex-col gap-3 pl-6">
                          {doc.items.map((item) => (
                            <li key={item.chinese}>
                              <Say line={item} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </section>
              ))}
              <div className="border-t border-line pt-4">
                <Say line={data.thanks} level="unit" />
              </div>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
