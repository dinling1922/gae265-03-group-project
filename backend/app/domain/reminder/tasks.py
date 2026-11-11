"""提醒信的 Celery 工作（ADR-0028）：只做轉接，規則與寄信都在 `service.py`。

- 掃描：beat 每 `REMINDER_SCAN_SECONDS` 秒排一次，找出到期的保單，每張各排一個寄信工作。
- 寄信：SMTP 失敗時自動重試，間隔逐次拉長；寄出成功才把計數加 1。
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.domain.reminder.mailer import SmtpMailer
from app.domain.reminder.service import due_policy_ids, send_reminder
from app.models.base import utc_now
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _run[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """每個工作開自己的事件迴圈與連線（不共用連線池：連線不能跨事件迴圈）。"""

    async def main() -> T:
        engine = create_async_engine(
            settings.SQLALCHEMY_DATABASE_URI, poolclass=NullPool
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


@celery_app.task(name="app.reminder.scan")
def scan_due_reminders() -> int:
    ids = _run(lambda session: due_policy_ids(session, utc_now()))
    for policy_id in ids:
        send_reminder_task.delay(str(policy_id))
    logger.info("提醒信掃描：%d 張保單到期", len(ids))
    return len(ids)


@celery_app.task(
    name="app.reminder.send",
    # smtplib 的錯誤都是 OSError 的子類別；連線逾時、對方拒絕都會重試
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
)
def send_reminder_task(policy_id: str) -> bool:
    sent = _run(
        lambda session: send_reminder(
            session, uuid.UUID(policy_id), utc_now(), SmtpMailer()
        )
    )
    logger.info("提醒信 %s：%s", policy_id, "已寄出" if sent else "不需要寄")
    return sent
