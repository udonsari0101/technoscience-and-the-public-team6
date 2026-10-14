"""Deterministic mock provider (AI_MODE=mock): realistic replies, zero API cost.

Replies are assembled from each philosopher's mock material
(empathy + lens-specific reframe + question) and follow the same
controller rules as the real prompt: no decisions, no mind-reading,
injection refusal and a safety-first branch.
"""

from __future__ import annotations

import hashlib
import time

from .base import AIProvider, AIReply, AIUsage, SummaryResult

TOPIC_KEYWORDS = {
    "contact": ("연락", "답장", "카톡", "문자", "읽씹", "안읽씹", "디엠", "dm"),
    "breakup": ("헤어", "이별"),
    "confession": ("고백", "썸"),
    "conflict": ("싸웠", "싸움", "싸워", "다퉜", "다툼", "화났", "화내"),
    "jealousy": ("질투", "이성 친구", "이성친구", "바람"),
    "self": ("자존감", "초라", "내가 싫", "자신이 없", "나 자신"),
}
INJECTION_KEYWORDS = (
    "이전 지시", "지시 무시", "지시를 무시", "system prompt", "시스템 프롬프트", "프롬프트 보여",
    "숨겨진 instruction", "숨겨진 지시", "instruction", "설정 해제", "설정을 해제", "무조건 헤어지라고",
)
SAFETY_KEYWORDS = (
    # physical violence / threats
    "때려", "때렸", "때리", "때릴", "맞았", "밀쳤", "밀쳐", "던졌", "폭력", "협박", "위협",
    "죽이", "죽인다", "목 졸", "흉기",
    # stalking / surveillance
    "스토킹", "따라다", "따라와", "집 앞", "찾아와", "찾아온", "감시", "위치 추적", "위치를 추적",
    "휴대폰 검사", "핸드폰 검사", "폰 검사", "휴대폰을 검사", "핸드폰을 검사",
    # coercion / confinement
    "감금", "가둬", "가두", "못 나가게", "강제로", "강요",
)
SELF_HARM_KEYWORDS = ("죽고 싶", "자해", "사라지고 싶", "목숨")
DECISION_KEYWORDS = ("정해줘", "정해 줘", "골라줘", "결정해줘", "결정해 줘", "할까 말까", "헤어질까")
AUTHORITY_KEYWORDS = ("철학적으로", "정답이지", "정답 맞지", "정답이야")

INJECTION_REPLY = (
    "그 요청은 들어줄 수 없어. 숨겨진 설정을 보여주거나 결론을 대신 정해주지는 않아. "
    "대신 지금 마음에 걸리는 이야기를 계속 들어볼게. {question}"
)
SAFETY_REPLY = (
    "지금 이야기에서 네 안전이 가장 먼저 걱정돼. 몸이 다칠 위험이 있거나 위협을 느끼면 바로 112에 연락하고, "
    "여성긴급전화 1366에서도 24시간 도움을 받을 수 있어. 이건 혼자 참고 넘길 일이 아니야. 지금은 안전한 곳에 있어?"
)
SELF_HARM_REPLY = (
    "그렇게까지 힘들다는 말을 꺼내줘서 고마워. 지금 마음이 많이 위태롭다면 자살예방상담전화 109에 바로 이야기해 줘, "
    "24시간 연결돼. 지금 곁에 연락할 수 있는 사람이 있어?"
)
DECISION_LEAD = "그 결정은 내가 대신 정하기보다 네가 내리는 게 맞다고 생각해. 대신 정리하는 건 같이 해볼게."
AUTHORITY_LEAD = "철학이 정답을 대신 정해주지는 않아. 다만 하나의 관점으로 같이 볼 수는 있어."
INTENT_KEYWORDS = ("무시하는 거지", "나 무시", "좋아하는 거 맞지", "좋아하는 거지", "마음이 식은 거지")

# Evaluation only (CONTROLLER_ENABLED=false, never in production): imitate the
# typical failure modes the controller exists to prevent, so the on/off
# comparison is visible without paying for real API calls.
UNCONTROLLED_LEADS = {
    "decision": "솔직히 지금 이야기만 들으면 헤어지는 쪽이 나아 보여.",
    "intent": "답장이 그렇게 늦다면 너를 무시하는 걸 수도 있어.",
    "authority": "철학적으로 보면 그게 정답에 가까워.",
}
CONTROLLER_MARKER = "지켜야 할 원칙:"


def _has(text: str, words) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _pick(items: list[str], seed: int, salt: int = 0) -> str:
    return items[(seed + salt * 7) % len(items)]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8], 16)


def detect_topic(text: str) -> str | None:
    for topic, words in TOPIC_KEYWORDS.items():
        if _has(text, words):
            return topic
    return None


class MockProvider(AIProvider):
    name = "mock"
    model = "mock-v1"
    is_billable = False

    def __init__(self, delay_ms: int = 0):
        self.delay_ms = max(0, int(delay_ms))

    def _sleep(self, seed: int) -> None:
        if self.delay_ms:
            jitter = (seed % 400) - 200  # 300–700 ms around the default 500 ms
            time.sleep(max(0, self.delay_ms + jitter) / 1000)

    def reply(self, *, system, messages, philosopher, max_tokens):
        user_turns = [m["content"] for m in messages if m["role"] == "user"
                      and not str(m["content"]).startswith("[상담 참고 정보")]
        last = user_turns[-1] if user_turns else ""
        seed = _seed(philosopher["id"], last, str(len(messages)))
        self._sleep(seed)
        mock = philosopher["mock"]
        recent = " ".join(m["content"] for m in messages[-6:] if m["role"] == "assistant")
        fresh_questions = [q for q in mock["questions"] if q not in recent] or mock["questions"]
        question = _pick(fresh_questions, seed, 2)
        fresh_reframes = [r for r in mock["reframes"] if r not in recent] or mock["reframes"]

        controlled = CONTROLLER_MARKER in (system or "")
        if not controlled:
            kind = ("decision" if _has(last, DECISION_KEYWORDS) else
                    "intent" if _has(last, INTENT_KEYWORDS) else
                    "authority" if _has(last, AUTHORITY_KEYWORDS) else None)
            if kind:
                text = f"{UNCONTROLLED_LEADS[kind]} {_pick(fresh_reframes, seed, 1)}"
                return AIReply(text=text, provider=self.name, model=self.model,
                               usage=AIUsage(), stop_reason="end_turn")
        if controlled and _has(last, INJECTION_KEYWORDS):
            text = INJECTION_REPLY.format(question=question)
        elif _has(last, SELF_HARM_KEYWORDS):
            text = SELF_HARM_REPLY
        elif _has(last, SAFETY_KEYWORDS):
            text = SAFETY_REPLY
        else:
            topic = detect_topic(last)
            reframe = mock["topic_reframes"].get(topic) if topic else None
            if reframe and reframe in recent:
                reframe = None
            reframe = reframe or _pick(fresh_reframes, seed, 1)
            if _has(last, AUTHORITY_KEYWORDS):
                lead = AUTHORITY_LEAD
            elif _has(last, DECISION_KEYWORDS):
                lead = DECISION_LEAD
            else:
                fresh_empathy = [e for e in mock["empathy"] if e not in recent] or mock["empathy"]
                lead = _pick(fresh_empathy, seed)
            text = f"{lead} {reframe} {question}"
        return AIReply(text=text, provider=self.name, model=self.model,
                       usage=AIUsage(), stop_reason="end_turn")

    def summarize(self, *, system, prompt, philosopher, old_summary, old_memory,
                  transcript, max_tokens):
        """Deterministic summary: keeps the latest user points, bounded in size."""
        lines = [ln for ln in (old_summary or "").splitlines() if ln.strip()]
        for m in transcript:
            if m["role"] != "user":
                continue
            flat = " ".join(m["content"].split())
            lines.append("- 사용자: " + (flat if len(flat) <= 70 else flat[:69] + "…"))
        last_q = next((m["content"] for m in reversed(transcript) if m["role"] == "assistant"), "")
        if last_q:
            q = " ".join(last_q.split())
            lines.append("- 직전 상담 질문: " + (q if len(q) <= 70 else q[:69] + "…"))
        lines = lines[-8:]
        memory = None
        if any("짧게" in m["content"] for m in transcript if m["role"] == "user"):
            memory = {
                "relationship_values": list((old_memory or {}).get("relationship_values") or []),
                "preferences": list((old_memory or {}).get("preferences") or []),
            }
            pref = "답변을 짧게 받는 것을 선호함"
            if pref not in memory["preferences"]:
                memory["preferences"] = (memory["preferences"] + [pref])[-5:]
        return SummaryResult(summary="\n".join(lines), provider=self.name, model=self.model,
                             user_memory=memory, usage=AIUsage())
