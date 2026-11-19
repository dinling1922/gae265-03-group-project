"""list_options：可用的目的地與在意事項（唯讀，固定清單）。

清單就是程式裡的列舉（`Destination`、`Concern`），順序同網頁畫面；沒有資料庫查詢、不含使用者資料。
讓 AI 在問使用者之前就知道有哪些選項（ADR-0034）。
"""

from pydantic import BaseModel, Field

from app.domain.benefits import Concern
from app.domain.destinations import Destination
from app.mcp_server.registry import mcp_tool

MAX_CONCERNS = 3  # 同網頁比較流程：最多選 3 項、不重複


class ListOptionsOut(BaseModel):
    """允許清單：只有這些欄位會離開後端。"""

    destinations: list[str] = Field(description="可用的目的地，順序同網頁")
    concerns: list[str] = Field(description="可選的在意事項，順序同網頁")
    max_concerns: int = Field(description="一次比較最多可選幾項在意事項")


@mcp_tool
async def list_options() -> ListOptionsOut:
    """列出建立保險推薦時可用的目的地與在意事項（固定清單，不含任何使用者資料）。

    目的地與在意事項都只能從清單裡選；一次比較的在意事項最多 max_concerns 項，依重要程度排序、不可重複。
    使用者的說法對不上清單時，請先向使用者確認，不要自行猜測。
    """
    return ListOptionsOut(
        destinations=[d.value for d in Destination],
        concerns=[c.value for c in Concern],
        max_concerns=MAX_CONCERNS,
    )
