"""Deterministic mock provider (AI_MODE=mock): realistic replies, zero API cost.

Replies are assembled from each philosopher's mock material
(empathy + question for short remarks, empathy + one lens-specific idea +
question otherwise; about 50–140 characters) and
follow the same controller rules as the real prompt: no decisions, no
mind-reading, no sugar-coating, injection refusal and a safety-first branch.
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

INJECTION_REPLY = "그 요청은 들어줄 수 없어. 대신 마음에 걸리는 이야기는 계속 들을게. {question}"
SAFETY_REPLY = (
    "지금 이야기에서 네 안전이 가장 먼저 걱정돼. 몸이 다칠 위험이 있거나 위협을 느끼면 바로 112에 연락하고, "
    "여성긴급전화 1366에서도 24시간 도움을 받을 수 있어. 이건 혼자 참고 넘길 일이 아니야. 지금은 안전한 곳에 있어?"
)
SELF_HARM_REPLY = (
    "그렇게까지 힘들다는 말을 꺼내줘서 고마워. 지금 마음이 많이 위태롭다면 자살예방상담전화 109에 바로 이야기해 줘, "
    "24시간 연결돼. 지금 곁에 연락할 수 있는 사람이 있어?"
)
DECISION_LEAD = "내가 대신 정하면 네가 정말 중요하게 여기는 게 빠질 수 있어."
AUTHORITY_LEAD = "철학이 정답을 대신 정해주지는 않아."
# Don't sugar-coat: when the user blames themselves for what the partner did,
# say plainly that the behaviour itself can be the problem.
WRONGDOING_KEYWORDS = ("거짓말", "속였", "속이", "몰래", "바람피", "바람을 피", "막말", "욕했", "욕을 해",
                       "무시했", "무시해", "잠수")
SELF_BLAME_KEYWORDS = ("내 탓", "내탓", "내 잘못", "내가 예민", "내가 너무", "내가 문제", "내가 의심")
HONEST_LEAD = "돌아보려는 마음은 좋지만, 상대가 한 행동의 책임까지 네가 질 필요는 없어."
HONEST_CORE = "그런 일이 반복된다면 네가 예민해서라기보다 신뢰를 깎는 행동 자체가 문제일 수 있어."
HONEST_QUESTION = "이 관계에서 네가 받아들일 수 있는 것과 없는 건 각각 뭐야?"
# Messages this short ("고마워", "요즘 마음이 복잡해") get a lead + question only.
SHORT_MESSAGE_CHARS = 20
THANKS_KEYWORDS = ("고마워", "고맙", "감사", "땡큐")
THANKS_LEAD = "도움이 됐다니 다행이야."
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


def _keywords(text: str) -> set[str]:
    """Rough content words (3+ chars) used to avoid saying the same idea twice."""
    words = {w.strip(".,?!'\"·") for w in text.split()}
    return {w for w in words if len(w) >= 3}


def _has(text: str, words) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _pick(items: list[str], seed: int, salt: int = 0) -> str:
    return items[(seed + salt * 7) % len(items)]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8], 16)


# One sentence per reply that names a signature idea in everyday words, so the
# mock answers (like the real prompt) make it obvious which philosopher speaks.
SIGNATURES = {
    "plato": [
        "사랑이 좋은 것을 늘 곁에 두고 싶은 마음이라면, 지금 네가 붙잡고 싶은 좋음이 핵심일 거야.",
        "눈에 보이는 행동은 그림자 같은 거라서, 그 뒤에서 네가 진짜 바라는 걸 찾아야 고민이 풀릴 때가 많아.",
        "끌림은 겉모습에서 시작해도 그 사람의 됨됨이, 함께 바라보는 가치로 깊어질 수 있어.",
        "내가 답을 정해 주기보다, 질문을 따라가다 보면 네가 이미 품고 있던 답을 스스로 꺼내게 될 거야.",
    ],
    "aristotle": [
        "사람은 반복하는 행동대로 만들어져서, 한 번의 말보다 쌓인 습관이 관계를 더 잘 보여줘.",
        "지나치게 참는 것과 지나치게 몰아붙이는 것 사이 어딘가에 너에게 맞는 중용이 있어.",
        "관계는 이익 때문에, 즐거움 때문에, 서로의 사람됨을 아껴서 이어지는데 오래가는 건 대개 마지막 경우야.",
        "좋은 연인은 서로 잘되길 바라는 또 하나의 나라서, 이 사람 곁에서 네가 더 너다워지는지가 중요해.",
    ],
    "epicurus": [
        "바람에는 꼭 필요한 것, 없어도 되는 것, 불안이 부풀린 것이 섞여 있어서 나눠 보면 가벼워져.",
        "내가 말하는 즐거움은 들뜬 쾌락이 아니라 마음이 흔들리지 않는 평온, 아타락시아에 가까워.",
        "지금 불안을 덜어 주는 선택과 며칠 뒤에도 편안한 선택은 다를 수 있으니 둘을 나란히 놓고 따져 보자.",
        "삶을 기쁘게 하는 것 중에 믿을 수 있는 우정만 한 게 없어서, 이 관계가 그런 안심을 주는지 궁금해.",
    ],
    "nietzsche": [
        "참는 게 착함처럼 보여도 속에서 원망, 르상티망으로 쌓인다면 너를 지키는 방식이 아닐 수 있어.",
        "남이 정해 준 좋은 연애의 기준보다 네가 스스로 세운 기준으로 살 때 비로소 너 자신이 되어 가는 거야.",
        "지금의 이 관계를 똑같이 끝없이 되풀이해야 한다면 기꺼이 그러겠다고 말할 수 있을지 한번 떠올려 봐.",
        "사랑은 결국 긴 대화 같은 거라서, 나이 들어서도 이 사람과 계속 이야기 나누고 싶은지가 중요한 물음이야.",
    ],
}

TOPIC_TITLES = {
    "contact": "연락 문제로 서운함",
    "breakup": "헤어질지 고민",
    "confession": "고백할지 고민",
    "conflict": "반복되는 다툼",
    "jealousy": "질투와 불안",
    "self": "연애 속 자존감",
}


def mock_title(first_message: str) -> str:
    """Topic-based short title (mock mode stands in for the model's title)."""
    topic = detect_topic(first_message)
    if topic:
        return TOPIC_TITLES[topic]
    flat = " ".join(first_message.split())
    for stop in (".", "?", "!", ",", "…"):
        flat = flat.split(stop)[0]
    return flat if len(flat) <= 14 else flat[:13].rstrip() + "…"


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

    def reply(self, *, system, messages, philosopher, max_tokens, want_title=False):
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
        elif _has(last, WRONGDOING_KEYWORDS) and _has(last, SELF_BLAME_KEYWORDS):
            text = " ".join((HONEST_LEAD, HONEST_CORE, HONEST_QUESTION))
        else:
            topic = detect_topic(last)
            authority = _has(last, AUTHORITY_KEYWORDS)
            decision = _has(last, DECISION_KEYWORDS)
            if authority:
                lead = AUTHORITY_LEAD
            elif decision:
                lead = DECISION_LEAD
            else:
                fresh_empathy = [e for e in mock["empathy"] if e not in recent] or mock["empathy"]
                lead = _pick(fresh_empathy, seed)
            if len(last.strip()) <= SHORT_MESSAGE_CHARS and not (topic or authority or decision):
                # A short remark gets a short reply, like the live style rule.
                if _has(last, THANKS_KEYWORDS):
                    lead = THANKS_LEAD
                text = " ".join((lead, question))
            else:
                # One idea in between: the philosopher's take on the topic when
                # there is one, otherwise a signature idea.
                core = mock["topic_reframes"].get(topic) if topic else None
                if core and core in recent:
                    core = None
                if not core:
                    signatures = SIGNATURES.get(philosopher["id"], [])
                    fresh_signatures = [s for s in signatures if s not in recent] or signatures
                    core = _pick(fresh_signatures, seed, 3) if signatures else _pick(fresh_reframes, seed, 1)
                if _keywords(core) & _keywords(question):
                    # Don't ask about the very idea the previous sentence just raised.
                    distinct = [q for q in fresh_questions if not (_keywords(core) & _keywords(q))]
                    question = _pick(distinct, seed, 2) if distinct else question
                text = " ".join((lead, core, question))
        return AIReply(text=text, provider=self.name, model=self.model,
                       usage=AIUsage(), stop_reason="end_turn",
                       title=mock_title(user_turns[0]) if want_title and user_turns else None)

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
