"""使用者自己的資料（ADR-0009）：刪除我的所有資料。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import delete, update
from sqlmodel import col

from app.api.deps import CurrentUser, SessionDep, get_google_oauth_client
from app.core.config import settings
from app.core.google_grants import grant_of, revoke_grant
from app.core.google_oauth import GoogleOAuthClient
from app.models import (
    ApiToken,
    Comparison,
    GoogleGrant,
    LineBindCode,
    LineBinding,
    Policy,
)

router = APIRouter(tags=["me"])


def optional_google_client() -> GoogleOAuthClient | None:
    """Google 沒設定（例如只用測試登入）時回 None：那種環境不會有真的授權，不必撤銷。
    測試用 `app.dependency_overrides[optional_google_client]` 換成假的。"""
    if not settings.google_login_enabled:
        return None
    return get_google_oauth_client()


OptionalGoogleDep = Annotated[GoogleOAuthClient | None, Depends(optional_google_client)]


@router.delete("/me/data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_data(
    session: SessionDep, user: CurrentUser, google: OptionalGoogleDep
) -> Response:
    """刪除這位使用者的所有資料：保單（連帶對話、訊息、事件、待辦、文件紀錄、送件紀錄）、
    方案比較與 Google 授權，解除 LINE 綁定，並撤銷所有存取權杖。

    保留 `user` 與登入身分 `identity`：使用者仍可登入，只是回到沒有資料的狀態；
    要連帳號都不留，是另一個決定（目前沒有需求）。
    Google 授權先請 Google 撤銷 refresh token 再刪紀錄；已存進使用者自己 Drive 的文件與
    Gmail 草稿不動（ADR-0021），使用者可以自己在 Drive 與 Gmail 刪除。
    """
    grant = await grant_of(session, user.id)
    if grant is not None and grant.refresh_token_encrypted is not None and google:
        await revoke_grant(session, grant, google)  # Google 暫時失敗也照樣清空 token
    await session.exec(delete(Policy).where(col(Policy.user_id) == user.id))
    await session.exec(delete(Comparison).where(col(Comparison.user_id) == user.id))
    await session.exec(delete(GoogleGrant).where(col(GoogleGrant.user_id) == user.id))
    # LINE 綁定（ADR-0033）：刪綁定與綁定碼；綁定用的權杖由下面的「撤銷全部」一併撤銷
    await session.exec(delete(LineBinding).where(col(LineBinding.user_id) == user.id))
    await session.exec(delete(LineBindCode).where(col(LineBindCode.user_id) == user.id))
    # 存取權杖（ADR-0032）：刪資料後外部代理不能再讀；權杖紀錄留著，使用者仍看得到已撤銷
    await session.exec(
        update(ApiToken)
        .where(col(ApiToken.user_id) == user.id, col(ApiToken.revoked_at).is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
