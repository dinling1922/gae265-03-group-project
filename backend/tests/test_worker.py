from app.core.config import Settings
from app.worker import celery_app, ping


def test_測試環境的工作直接執行_不連_redis() -> None:
    assert celery_app.conf.task_always_eager is True
    assert ping.delay().get() == "pong"


def test_工作做完才確認_不保存結果() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_ignore_result is True
    assert conf.task_time_limit > conf.task_soft_time_limit


def test_提醒信間隔的預設值是規格的_24_小時與_3_天() -> None:
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.REMINDER_FIRST_DELAY_SECONDS == 24 * 60 * 60
    assert s.REMINDER_INTERVAL_SECONDS == 3 * 24 * 60 * 60
    assert s.MAIL_FROM_NAME == "好險有你 Tripcare"
