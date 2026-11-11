"""Celery 背景工作（ADR-0028）：提醒信的排程與寄送。

啟動方式見 compose.yaml：
- worker：`celery -A app.worker:celery_app worker`
- beat：`celery -A app.worker:celery_app beat`
- Flower：`celery -A app.worker:celery_app flower`

Redis 只當佇列：工作結果不保存，提醒信寄到第幾封等狀態都在 Postgres。
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery("app", broker=settings.REDIS_URL)

celery_app.conf.update(
    # 不保存工作結果，所以不需要 result backend
    task_ignore_result=True,
    # 工作做完才確認；worker 中途被殺掉，工作會回到佇列重做
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # 一個工作最多跑多久：超過 soft 先丟例外讓工作收尾，超過 hard 直接結束子程序
    task_soft_time_limit=90,
    task_time_limit=120,
    # 每個子程序處理這麼多個工作就換新的，避免記憶體慢慢膨脹（B2s 只有 4 GB）
    worker_max_tasks_per_child=100,
    # Redis 還沒好時，worker 啟動會重試連線，不直接結束
    broker_connection_retry_on_startup=True,
    timezone="Asia/Taipei",
    enable_utc=True,
    # 測試不連 Redis：送出的工作直接在同一個 process 執行
    task_always_eager=settings.ENVIRONMENT == "test",
    task_eager_propagates=True,
    # 提醒信的掃描工作在後續的 PR 加進來
    beat_schedule={},
)


@celery_app.task(name="app.worker.ping")
def ping() -> str:
    """連線檢查：worker 收得到工作就回 pong。"""
    return "pong"
