# 條款問答：API × 推理強度量測（2026-11-23，gpt-6-luna）

對應 issue #591。**只量測，沒有改產品程式、設定與正式站**（`adapters.py`、`llm.py`、`config.py`、`.env` 都沒動）。

## 結論

**維持 `reasoning_effort="none"`（Chat Completions＋工具）。**

1. **Chat Completions＋工具：只有 none 能用。** low、medium、high、xhigh 每次都是 400（錯誤原文見下），預設的 medium 也一樣，所以現在才要設 `INCIDENT_QA_REASONING_EFFORT=none`。
2. **Responses API 五種強度（含 none）都能用，但沒有看到品質上的好處。** 3 題 × 5 種強度 × 10 次，Q1、Q2 都是 10/10 白話，Q3 都回到「保單沒寫」的意思；Q1 沒有任何一次自己算次數或金額。推理強度越高，只有秒數與 token 變多。
3. **代價**：Q1 平均秒數 none 4.2 → high 5.9 → xhigh 7.3；推理 token 約 0 → 154 → 248（輸出總 token 約 129 → 289 → 381）。xhigh 在 30 次裡有 1 次卡了 180 秒逾時才重試成功（總共 185.8 秒）；目前產品的模型逾時設定是 30 秒（`llm.py` 的 `REQUEST_TIMEOUT`），這種情況會直接失敗、退回貼原文。
4. **改用 Responses API 不是只改一個設定**：這個環境上要用 `2025-04-01-preview`（舊的 `2024-10-21` 是 404）、`store=False`，而且 Responses API 的 `content` 是區塊清單，`agent_answerer` 只認字串，不處理的話**每一次都因為「沒有產生文字」退回貼原文**（見下方 raw 那一列，3/3）。正式站的 deployment 支不支援這個 API 版本也要另外確認。
5. 這次只量了 3 題（兩題容易、一題要回「沒寫」），題目偏簡單；比較難的問題（要整合多條條文、結論有分歧）沒有量，所以「高強度沒有好處」只能說在這 3 題上沒有。如果之後要換，另開 issue，在錄影與 12/3 發表之後。

## 做法

- 環境：本機 docker 後端，部署 `gpt-6-luna`（和正式站同一款模型），`azure_openai`。Chat Completions 用設定預設的 API 版本 `2024-10-21`；Responses API 用 `2025-04-01-preview`（只在腳本裡指定；先試過 `2024-10-21`、`2025-06-01`、`2025-08-01-preview`、`preview`、`v1` 都是 404，`2025-03-01-preview` 也能用）。
- 情境：Demo B（安達進階型、颱風班機延誤，2027/09/06～09/11），用腳本走完 5 號的追問建出一個事件；每題直接呼叫 `adapters.agent_answerer`（`search_rules` 工具、`QA_PROMPT`、數字檢查都是產品原本的流程），只換模型的建法。
- Chat Completions：每種強度先各打 3 次第 1 題，確認是否出錯；沒出錯的（none）再完整量 3 題 × 10 次。
- Responses API：`use_responses_api=True`、`store=False`、`reasoning={"effort": …}`，每種強度 × 3 題 × 10 次。Responses 的 `content` 是區塊清單，腳本把模型包一層，把文字區塊攤平成字串（推理區塊丟掉）才能跑 `agent_answerer`；另外用沒攤平的版本打 3 次確認原樣用不了。
- 題目：Q1＝延誤 8 小時可以賠多少？（預期：說明每滿四小時給付、請看保單金額，不自己算次數）；Q2＝班機延誤申請理賠要準備哪些文件？（預期：白話列出文件）；Q3＝保險公司收到文件後，多久會把理賠金匯給我？（預期：保單未載明）。
- 分類：白話（有條文依據的說明）／保單未載明（固定訊息 `NOT_COVERED`）／貼原文／錯誤。每次回答同時 4 個請求並行，秒數比單打略慢；token 是一次回答裡所有模型呼叫（平均 2 次：查條文、回答）的加總。
- **共呼叫 198 次回答**（195 筆結果列入統計；另有 3 次 Chat none 的試打在補完整量測時被取代），約 **7 分鐘**（427 秒）；另外 Responses API 版本探測約 11 次小呼叫、腳本冒煙測試（各組合各 1 次）約 20 次回答，所以整個過程總共約 230 次模型呼叫、約 20 分鐘（含寫腳本的時間不算）。量測在開發資料庫建了 2 個測試帳號與 2 個 Demo B 事件（冒煙測試與正式量測各 1）。

## 結果表

欄位：「白話」只列 Q1、Q2（預期白話）；Q3 預期是「保單未載明」，列在「保單未載明」欄。「自己算次數」只看 Q1：回答有沒有自己算出這次給付幾次（引用條文上限「保險期間內限給付二次」不算）。

| API | 強度 | 題目 | 白話 | 保單未載明 | 貼原文 | 錯誤 | 自己算次數 | 平均秒 | 最慢秒 | 平均輸入／輸出／推理 token |
|---|---|---|---|---|---|---|---|---|---|---|
| chat | none | Q1 | 10 | — | 0 | 0 | 0 | 4.4 | 4.9 | 3168／131／0 |
| chat | none | Q2 | 10 | — | 0 | 0 | — | 4.4 | 5.1 | 3049／140／0 |
| chat | none | Q3 | — | 10 | 0 | 0 | — | 4.7 | 7.9 | 3906／106／0 |
| chat | low | Q1 | 0 | — | 0 | 3 | 0 | 1.9 | 1.9 | 0／0／0 |
| chat | medium | Q1 | 0 | — | 0 | 3 | 0 | 1.8 | 1.9 | 0／0／0 |
| chat | high | Q1 | 0 | — | 0 | 3 | 0 | 2.1 | 2.1 | 0／0／0 |
| chat | xhigh | Q1 | 0 | — | 0 | 3 | 0 | 2.4 | 3.0 | 0／0／0 |
| responses | none | Q1 | 10 | — | 0 | 0 | 0 | 4.2 | 4.8 | 3009／129／0 |
| responses | none | Q2 | 10 | — | 0 | 0 | — | 5.0 | 11.1 | 2944／135／0 |
| responses | none | Q3 | — | 7（另 3 次用自己的話說條文沒寫） | 0 | 0 | — | 4.2 | 6.8 | 3017／94／0 |
| responses | low | Q1 | 10 | — | 0 | 0 | 0 | 4.6 | 6.3 | 3007／128／0 |
| responses | low | Q2 | 10 | — | 0 | 0 | — | 4.5 | 5.2 | 2998／142／0 |
| responses | low | Q3 | — | 9（另 1 次用自己的話說條文沒寫） | 0 | 0 | — | 4.1 | 5.5 | 3018／85／0 |
| responses | medium | Q1 | 10 | — | 0 | 0 | 0 | 5.1 | 7.0 | 3011／234／94 |
| responses | medium | Q2 | 10 | — | 0 | 0 | — | 4.4 | 5.0 | 2897／161／22 |
| responses | medium | Q3 | — | 10 | 0 | 0 | — | 5.2 | 9.2 | 3383／175／91 |
| responses | high | Q1 | 10 | — | 0 | 0 | 0 | 5.9 | 6.5 | 3009／289／154 |
| responses | high | Q2 | 10 | — | 0 | 0 | — | 5.0 | 5.8 | 2935／194／52 |
| responses | high | Q3 | — | 10 | 0 | 0 | — | 5.3 | 6.8 | 3012／226／156 |
| responses | xhigh | Q1 | 10 | — | 0 | 0 | 0 | 7.3 | 11.1 | 3008／381／248 |
| responses | xhigh | Q2 | 10 | — | 0 | 0 | — | 23.7 | 185.8 | 2932／234／96 |
| responses | xhigh | Q3 | — | 10 | 0 | 0 | — | 7.0 | 10.0 | 3754／312／224 |
| responses(raw) | low | Q1 | 0 | — | 3 | 0 | 0 | 4.6 | 5.0 | 3013／130／0 |

補充：
- Chat Completions 的 low～xhigh 只試了 Q1 各 3 次（全部 400），所以沒有其他題的資料；錯誤回得快（約 2 秒），沒有 token 用量。
- Responses 的 none 3/10、low 1/10 在 Q3 沒有回固定的「保單未載明，請向保險公司確認」，而是用自己的話說「條文沒有載明保險公司收到文件後多久匯款，無法依條文確認」，意思是對的（沒有編時間），只是文字不是固定訊息；medium、high、xhigh 與 Chat none 都是 10/10 固定訊息。
- Q1 回答裡有 11 次引用「保險期間內（最多）給付二次」，是條文原文的上限，不是自己算（腳本一開始把其中 2 次誤標成疑似算次數，人工複查後排除）。
- xhigh Q2 有 1 次 185.8 秒：請求卡住到 180 秒逾時，重試一次後 5.8 秒成功；其餘 29 次都在 11 秒內（Q2 平均 23.7 秒是這一次拉高的）。
- 沒有出現「答不了」（腳本有這個分類，#591 背景裡 Responses 預設值曾有 9 次 8 次只回「條文答不了」，這次攤平 content 後沒有重現）。

## 錯誤訊息原文

low、medium、high、xhigh 四種強度在 Chat Completions＋工具上全部是同一個 400（每種各 3 次，共 12 次），原文：

```
OpenAIInvalidRequestError: Error code: 400 - {'error': {'message': "Function tools with reasoning_effort are not supported for this model in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.", 'type': 'invalid_request_error', 'param': 'reasoning_effort', 'code': None}}
```

## 每種組合的代表回答

- chat／none／Q1（白話，4.1 秒）：條文規定班機延誤每滿四小時給付一次，並受每次事故最高給付金額限制；實際金額依保單所載保險金額計算，請查看保單上的金額。實際以保險公司審核為準。
- chat／none／Q2（白話，4.5 秒）：班機延誤理賠應準備： - 理賠申請書 - 機票及登機證，或航空業者出具的搭機證明 - 航空業者出具、載明班機延誤期間的證明 實際以保險公司審核為準。
- chat／none／Q3（保單未載明，4.2 秒）：保單未載明，請向保險公司確認
- chat／low／Q1（錯誤，1.9 秒）：OpenAIInvalidRequestError: Error code: 400 - {'error': {'message': "Function tools with reasoning_effort are not supported for this model in /v1/chat/completions. To use function tools, use /v1/respon
- chat／medium／Q1（錯誤，1.9 秒）：OpenAIInvalidRequestError: Error code: 400 - {'error': {'message': "Function tools with reasoning_effort are not supported for this model in /v1/chat/completions. To use function tools, use /v1/respon
- chat／high／Q1（錯誤，2.1 秒）：OpenAIInvalidRequestError: Error code: 400 - {'error': {'message': "Function tools with reasoning_effort are not supported for this model in /v1/chat/completions. To use function tools, use /v1/respon
- chat／xhigh／Q1（錯誤，3.0 秒）：OpenAIInvalidRequestError: Error code: 400 - {'error': {'message': "Function tools with reasoning_effort are not supported for this model in /v1/chat/completions. To use function tools, use /v1/respon
- responses／none／Q1（白話，4.7 秒）：條文規定班機延誤期間每滿四小時給付一次，金額依保單所載；每次事故最高給付金額也依保單所載，保險期間內以給付二次為限。請查看保單上的給付金額。實際以保險公司審核為準。
- responses／none／Q2（白話，11.1 秒）：依條款申請班機延誤理賠，應準備： - 理賠申請書 - 機票及登機證，或航空業者出具的搭機證明 - 航空業者出具、載明班機延誤期間的證明 實際以保險公司審核為準。
- responses／none／Q3（保單未載明，4.0 秒）：保單未載明，請向保險公司確認
- responses／low／Q1（白話，4.7 秒）：條文規定，班機延誤期間每滿四小時給付一次，實際給付金額依保單所載的每次金額及每次事故最高金額計算；你可查看保單確認金額。實際以保險公司審核為準。
- responses／low／Q2（白話，4.9 秒）：依條款，申請班機延誤理賠應檢具： - 理賠申請書 - 機票及登機證，或航空業者出具的搭機證明 - 航空業者出具、載明班機延誤期間的證明 程式結果顯示本次可能適用；實際以保險公司審核為準。
- responses／low／Q3（保單未載明，3.5 秒）：保單未載明，請向保險公司確認
- responses／medium／Q1（白話，5.2 秒）：條文規定，班機延誤期間每滿四小時，依保單所載金額給付；每次事故也有保單所載的最高給付金額，保險期間內以給付二次為限。請查看保單上的金額，實際以保險公司審核為準。
- responses／medium／Q2（白話，4.3 秒）：依條款第23條，申請班機延誤理賠應準備：理賠申請書、機票及登機證或航空業者出具的搭機證明，以及航空業者出具載明班機延誤期間的證明。實際以保險公司審核為準。
- responses／medium／Q3（保單未載明，9.2 秒）：保單未載明，請向保險公司確認
- responses／high／Q1（白話，5.9 秒）：條文規定班機延誤期間每滿四小時給付，給付金額及每次事故最高額依保單所載；保險期間內最多給付二次。請查看保單上的金額。實際以保險公司審核為準。
- responses／high／Q2（白話，4.6 秒）：申請時需準備：理賠申請書、機票及登機證（或航空業者出具的搭機證明），以及航空業者出具、載明班機延誤期間的證明。實際以保險公司審核為準。
- responses／high／Q3（保單未載明，4.8 秒）：保單未載明，請向保險公司確認
- responses／xhigh／Q1（白話，7.4 秒）：程式結果顯示可能適用。條文規定延誤期間每滿四小時給付一次，給付金額及每次事故最高額依保單所載，保險期間內限給付二次；請查看保單金額。實際以保險公司審核為準。
- responses／xhigh／Q2（白話，5.2 秒）：依條款，申請班機延誤理賠應備： 1. 理賠申請書。 2. 機票及登機證，或航空業者出具的搭機證明。 3. 航空業者出具、載明班機延誤期間的證明。 實際以保險公司審核為準。
- responses／xhigh／Q3（保單未載明，6.8 秒）：保單未載明，請向保險公司確認
- responses(raw)／low／Q1（貼原文，5.0 秒）：（無法產生白話說明，以下直接列出條款原文。） 【CHUBB05 第 21 條 承保範圍（第 3 頁）】 被保險人於本保險契約保險期間內，以乘客身分預定搭乘之定期航 班發生延誤，致被保險人實際出發時間較預定出發時間延誤四小時 以上者，本公司依本保險契約約定之保險金額給付保險金。 對於班機延誤之理賠金額，班機延誤期間每滿四小時給付金額及每 次事故最高給付金額，本公司依本保險契約所載保險金額給付保險 金，但保險期間內以給付二次為限。 班機延誤期間之計算，應符合下列情事之一，且若為同

## 偏離 #591 的地方

- Responses API 的 API 版本：#591 沒有指定，我試了幾個，用 `2025-04-01-preview`（見做法）。
- Responses API 的 `content` 是區塊清單，`agent_answerer` 原樣用不了，所以腳本把模型包一層攤平文字，沒有改 `adapters.py`；這也是上面「改用 Responses API 不是只改一個設定」的實測依據。
- Chat Completions 的 low～xhigh 只量了 Q1 各 3 次（#591 就是「先打 1～3 次確認」）。另外 Chat none 也完整量了 3 題 × 10 次當對照。
- token 用量取得了（LangChain 的 `usage_metadata`）。

## 附錄：腳本

`backend/app/eval/qa_reasoning_effort.py`（模組說明裡有完整用法）。在本機 docker 後端跑：

```bash
docker compose exec backend uv run python -m app.eval.qa_reasoning_effort \
    --runs 10 --probe 3 --raw-responses 3 --concurrency 4 \
    --out /tmp/qa-reasoning-effort.md --json /tmp/qa-reasoning-effort.json
docker compose cp backend:/tmp/qa-reasoning-effort.md .
```

參數：`--runs` 每題每種組合幾次；`--probe` Chat Completions 每種強度先試打幾次；`--raw-responses` 不攤平 content 的 Responses API 打幾次；`--responses-api-version` Responses API 的版本（預設 `2025-04-01-preview`）；`--concurrency` 同時幾個請求。會呼叫 LLM 供應商，不進 CI。
