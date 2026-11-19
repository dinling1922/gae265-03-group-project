"""create_comparison 抽取評估腳本本身（#510）：題組格式、判定與報告；不呼叫 LLM。"""

from datetime import date

from app.domain.trip_extraction import Fixed
from app.eval.trip_extraction import (
    QUESTIONS,
    Question,
    evaluate,
    load_questions,
    render_report,
)


def oracle(questions: list[Question]):
    """假的「完美 LLM」：照題目的標準答案回；資訊不足的題目就少給對應的欄位。"""
    by_request = {q.request: q for q in questions}

    async def extractor(request: str, _today: date, _fixed: Fixed) -> object:
        q = by_request[request]
        e = q.expect
        if e["status"] == "ok":
            return {
                "destination": e["destination"],
                "depart_date": e["depart_date"],
                "return_date": e["return_date"],
                "concerns": e["concerns"],
                "unsupported": ["最便宜"] if e.get("unsupported") else [],
            }
        missing = set(e["missing"])
        out: dict = {
            "destination": None if "destination" in missing else "日本",
            "depart_date": "2026-12-20",
            "return_date": "2026-12-25",
            "concerns": [] if "concerns" in missing else ["班機延誤"],
        }
        if "depart_date" in missing:
            out["depart_date"] = None
            out["return_date"] = None
            out["stated_days"] = None if "trip_length" in missing else 7
        elif "trip_length" in missing:
            out["return_date"] = None
        return out

    return extractor


def test_question_file_is_well_formed():
    today, questions = load_questions()
    assert QUESTIONS.exists() and today == date(2026, 11, 19)
    assert len(questions) >= 20
    ids = [q.id for q in questions]
    assert len(set(ids)) == len(ids)
    assert {"目的地與日期", "相對日期", "在意事項", "不支援需求", "資訊不足"} <= {
        q.category for q in questions
    }
    for q in questions:
        assert q.expect["status"] in ("ok", "needs_more_info"), q.id
        if q.expect["status"] == "ok":
            assert {"destination", "depart_date", "return_date", "concerns"} <= set(
                q.expect
            )
            d, r = (
                date.fromisoformat(q.expect["depart_date"]),
                date.fromisoformat(q.expect["return_date"]),
            )
            assert 4 <= (r - d).days + 1 <= 10 and d >= today, q.id
        else:
            assert q.expect["missing"], q.id


async def test_script_runs_with_a_fake_llm_and_reports_provider():
    today, questions = load_questions()
    results = await evaluate(questions, today, oracle(questions), runs=2)
    assert all(o.passed for runs in results for o in runs), [
        (o.question.id, o.errors, o.failure)
        for runs in results
        for o in runs
        if not o.passed
    ]
    report = render_report(
        results,
        day=date(2026, 11, 20),
        today=today,
        provider="fake",
        model="oracle",
        runs=2,
    )
    assert "供應商：fake" in report and "2026-11-20" in report
    assert f"通過：{len(questions) * 2}／{len(questions) * 2}" in report


async def test_wrong_answers_and_llm_failures_are_reported():
    today, questions = load_questions()

    async def wrong(request: str, _today: date, _fixed: Fixed) -> object:
        if "曼谷" in request:
            raise RuntimeError("boom")
        return {
            "destination": "日本",
            "depart_date": "2026-12-20",
            "return_date": "2026-12-25",
            "concerns": ["班機延誤"],
        }

    results = await evaluate(questions, today, wrong)
    outcomes = {runs[0].question.id: runs[0] for runs in results}
    assert outcomes["e03"].failure and "RuntimeError" in outcomes["e03"].failure
    assert not outcomes["e02"].passed and any(
        "destination" in e for e in outcomes["e02"].errors
    )
    assert not outcomes["e13"].passed
    report = render_report(
        results, day=today, today=today, provider="fake", model="m", runs=1
    )
    assert "## 不符的結果" in report and "呼叫失敗" in report
