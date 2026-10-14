# AskPhilo TODO

작업 진행 상태 문서입니다. (사용 방법은 README.md)

상태 표기: `[ ]` Pending · `[~]` In progress · `[x]` Complete · `[!]` Blocked(이유 기재)

완료된 항목도 지우지 않고 남겨서 진행 이력을 보존합니다.

---

## Phase 0 — Render Deployment
- [x] Repository 분석 (GitHub repo 비어 있음 · PUBLIC · 로컬은 git 미초기화)
- [x] Firebase JSON을 `secrets/`로 이동 + `.gitignore`로 차단
- [x] Flask app factory (`askphilo/`) + `wsgi.py`
- [x] `GET /health` (Firestore/AI 호출 없음, secret 미노출)
- [x] Landing 화면 + static CSS + favicon/logo
- [x] `gunicorn.conf.py` (1 worker · 8 threads · no preload)
- [x] `requirements.txt` / `requirements-dev.txt` / `.python-version`
- [x] `render.yaml` (free · singapore · main · /health · AI_MODE 초기값 mock, 이후 Dashboard 소유 `sync: false`)
- [x] `.env.example`
- [x] Secret 검사 스크립트 + pre-commit hook (`scripts/check_secrets.py`)
- [x] pytest (health / config / static / headers)
- [x] local smoke test (Flask dev server + WSL gunicorn production 설정)
- [x] git init + 첫 commit `chore: bootstrap render deployment` + main push
- [x] commit에서 Claude co-author 제거 (repo 재생성 후 clean commit `2ed7626` push, 이후 commit에도 넣지 않음)
- [x] Render Blueprint 연결 (사용자 작업)
- [x] Render Secret File 업로드 (사용자 작업 — `/health`에서 `firebase_configured: true` 확인)
- [x] askphilo.kro.kr DNS + custom domain 검증 (사용자 작업)
- [x] production `/health` 확인 (askphilo.kro.kr · askphilo.onrender.com 모두 200)

## Firebase / Firestore
- [x] Firebase Admin SDK lazy 초기화 (worker 안에서, gRPC fork 안전)
- [x] Repository interface + Firestore 구현 + In-memory 구현(테스트/로컬)
- [x] Firestore 연결 local 검증 (`scripts/firestore_smoke.py`: 임시 user로 전체 연산 확인 후 데이터 정리)
- [x] `set_conversation_fields` (summary 재시도 시점 기록용)

## Authentication
- [x] 회원가입 UI (이름 · 아이디 · 비밀번호 · 비밀번호 확인)
- [x] username 규칙 (3~20자, 영문/숫자/_, 대소문자 무시)
- [x] 비밀번호 처리 (`services/passwords.py` 한 곳), 로그 미기록 테스트
- [x] `username_index` transaction으로 중복 가입 방지
- [x] 중복 username 동시 가입 테스트 (8 threads → 1명만 성공 / Firestore 중복 거부 확인)
- [x] 로그인 / 로그아웃 (HttpOnly · SameSite=Lax · production Secure cookie)
- [x] 로그인 필요 페이지 보호 (Home/Chat/History/Settings)
- [x] CSRF 보호 (form hidden field + `X-CSRF-Token` header)
- [x] 로그인/가입 rate limit (in-process, 1 worker)

## Home / Philosopher
- [x] Home (인사, 새 상담 CTA, 최근 대화 5개)
- [x] 철학자 데이터 (플라톤 · 아리스토텔레스 · 에피쿠로스 · 니체) — workflow: 초안 → 철학 정확성/규칙·톤 검토(41건) → 수정
- [x] 철학자 선택 카드 UI ("누구와 이야기해볼까요?", select box 없음)
- [x] 철학자 상세 (페이지 + bottom sheet: 보는 방식 · 잘 맞는 고민 · 질문 스타일)
- [x] 철학자 original SVG visual (일관된 스타일)
- [x] 한국어 조사 처리 (`플라톤과` / `니체와`)

## Conversation / Chat
- [x] Conversation 생성 (AI 호출 없음, 첫 메시지 때 생성 → 빈 대화 안 남김)
- [x] Message 저장 (message_index transaction, 문서 ID `000000`)
- [x] Title: 첫 user message 앞부분 (AI 호출 없음)
- [x] Chat UI (bubble, auto-resize textarea, sticky composer, safe-area, 100dvh)
- [x] Enter 전송 / Shift+Enter 줄바꿈 (desktop, 한글 IME 조합 중 Enter 무시)
- [x] Loading: typing dots, 전송 중 입력 잠금(readonly) · 전송 버튼 비활성화
- [x] 실패 시 사용자 message 보존 + 명시적 Retry (생성 중에는 retry 차단)
- [x] Conversation 다시 열기 (+ 이전 메시지 더 보기)
- [x] History 목록 (+ 더 보기)
- [x] Conversation 삭제 (messages subcollection 포함, 확인 dialog/페이지)
- [x] Soft limit 안내 / Hard limit 차단 (기존 대화는 읽기 가능)
- [x] 대화별 접근 제어 (다른 사용자 404)
- [x] iOS Safari(WebKit) / Android Chrome 에뮬레이션 + 키보드(viewport 축소) 검증 (`scripts/device_check.py`: iPhone SE·13·14 Pro Max, Galaxy S8, Pixel 7 — 65/65)
- [ ] Mobile chat keyboard 실제 기기 테스트 (iOS Safari / Android Chrome) — 사용자 기기 필요

## Memory / Context
- [x] Context builder (system + user memory + summary + recent messages)
- [x] Conversation summary (threshold 기반, 응답 후 실행, 실패해도 대화 지속)
- [x] Summary optimistic concurrency + 동시 실행 방지
- [x] Summary 실패 시 다음 threshold까지 재시도 안 함 (`summary_retry_after_count`)
- [x] Mock summary (deterministic)
- [x] User-level memory (최소, summary 요청 안에서만 갱신)
- [x] 새 conversation은 다른 대화 raw message/summary 미포함 (Scenario G 테스트)

## AI
- [x] AI provider interface
- [x] Mock AI (철학자별 realistic 응답, controller 규칙 준수, 최근 질문 반복 회피, pytest delay 0)
- [x] Prompt: base + controller + philosopher + style (간결, 사용자 내용은 messages로만)
- [x] controller_enabled true/false 비교 구조 (UI 비노출, `flask compare-controller`)
- [x] Anthropic provider (1 message = 1 call, `max_retries=0`, refusal server-side fallback)
- [x] Token usage 저장 (message + conversation `usage`)
- [x] Prompt caching (top-level automatic cache, append-only context)
- [x] Usage 확인 도구 (`flask usage`, `/dev/usage` development 전용)
- [x] `flask usage`를 실제 Firestore(collection group)에서 확인 (읽기 전용, index 불필요)
- [x] 실제 모델 평가 스크립트 `scripts/live_eval.py` (시나리오 A~G·안전, 호출 수·token 측정, `--yes`+`AI_MODE=anthropic` 없이는 호출 안 함, in-memory 저장)
- [x] Mock: 공감 문장도 최근 사용분 반복 회피, sheet 열 때 닫기 버튼 focus ring 제거
- [x] 기본 모델 `claude-opus-5-5`, `ANTHROPIC_EFFORT=low`, thinking headroom
- [x] 실제 Anthropic 호출 검증 (사용자 승인, `claude-sonnet-5-5`, local · in-memory): `live_eval.py --long` 19회 + style 조정 후 8회. 시나리오 A~G·안전 규칙 준수, 요약 1회(20 메시지), memory 갱신, prompt caching 동작(캐시 안 된 입력 ≈4 token/호출), 총 약 $0.12
- [x] Style prompt 조정: 한 문단 2~4문장·250자·질문 1개 (평균 출력 250→202 token, 300 상한 도달 감소)
- [x] Production `AI_MODE=anthropic` 전환 (사용자 요청) — `render.yaml`에 `AI_MODE=anthropic`, `ANTHROPIC_MODEL=claude-sonnet-5-5` 고정. 모델은 사용자 위임으로 Haiku/Sonnet 실측 비교 후 Sonnet(원칙 준수 13/16 vs 5/16, 답변 약 $0.004)

## 오늘의 질문 한도 표시
- [x] 일일 사용량을 Firestore user 문서에 저장 (KST 자정 초기화, transaction 예약 + 실패 시 환불)
- [x] 채팅 입력창 아래 "오늘 남은 질문", 홈 진행 막대, 설정 사용량
- [x] 소진 시 입력창 잠금 + "내일 0시에 다시 채워져요" 안내
- [x] 기존 in-process 일일 상한 제거 (분당·전체 시간당 상한은 유지)

## 대화 제목 · 철학자 일러스트 · 철학자다운 답변 (사용자 요청)
- [x] 대화 제목 자동 생성: 첫 답변 요청 안에서 짧은 제목을 함께 받음(추가 API 호출 없음), 요약 때 같은 요청에서 갱신, mock은 주제 기반 규칙
- [x] 대화 제목 직접 편집 (채팅 메뉴 → 이름 바꾸기), 사용자가 바꾼 제목은 자동 갱신하지 않음
- [x] 철학자 일러스트 프로필 (오리지널 flat illustration SVG, 4명 같은 화풍, 36px 식별 확인) + 시작 화면 얼굴 줄
- [x] 철학자다운 답변: 대표 개념(시그니처)을 매 답변에 일상어로 녹이기, controller 규칙 5 조정(개념 이름 허용, 자기소개·가짜 인용 금지 유지), mock도 대표 개념 문장 포함
- [x] System prompt를 `system_prompt/*.md` · `system_prompt/philosophers_prompt/*.md`로 분리 (주석 제거 loader, 누락 시 즉시 오류)

## 관리자 (사용자 요청)
- [x] `role: admin` + `/admin` 대시보드 (DB에서 매 요청 권한 확인, 비관리자 404, 로그인 시 대시보드로 이동)
- [x] 대시보드: 오늘·이번 달 API 사용량(`usage_daily`), 예상 비용·남은 예산(환경변수 단가·예산), 서비스 상한 잔여, 최근 14일, 사용자 목록
- [x] `scripts/create_admin.py`(비밀번호는 환경변수·입력으로만), `scripts/wipe_users.py`(dry run 기본)
- [x] 배포 후 기존 사용자 삭제(1명) + admin 계정 생성 (production Firestore, 로그인 시 `/admin` 이동 확인)
- [x] 대시보드 production 503 수정: `usage_daily`를 문서 ID 내림차순으로 조회하려면 Firestore index가 필요 → 최근 62일 날짜를 문서 ID로 직접 읽기(`get_all`)로 변경, 실제 Firestore smoke 통과
- [!] 비밀번호 저장 방식 변경 요청 — 작업 도구의 자동 권한 검사에서 차단되어 보류 (저장·확인 로직은 `services/passwords.py`로 모아 둠)
- [ ] 짧은 live 확인 (Sonnet 5.5, 철학자별 1회)

## 문구 정리 · 모바일 · 팀원 규칙 반영 (사용자 요청)
- [x] 첫 화면: 설명 문단과 하단 문구 삭제, 버튼은 "시작하기" / "로그인"만
- [x] 가입/로그인 제목 단순화("회원가입", "로그인"), 가입 직후 환영 toast 삭제(홈 인사와 중복), 아이디 안내 축약
- [x] 홈: 인사 한 줄, 새 상담 카드 부제 삭제, 대화가 없으면 '최근 대화' 영역 숨김, "0시에 채워져요"는 소진 시에만
- [x] 철학자 선택: 부제·설명·시대 삭제(상세에만 남김), 카드 압축(높이 약 35% 감소), 상세의 '이런 고민과 잘 맞아요' 삭제(주제 chip과 중복)
- [x] 채팅: 헤더 tagline 삭제(제목이 생기면 철학자 이름만), "이렇게 시작해 볼 수 있어요"·Enter 안내 삭제, 인사말 짧게, 입력창 "메시지 입력"
- [x] 설정: 이용 한도·상담 기억의 긴 안내 문구 축약
- [x] 모바일: 400px 이하에서 여백·채팅 아바타 축소로 말풍선 폭 확대, `touch-action: manipulation`, 페이지·메시지 애니메이션 0.2s, iOS식 키보드(visual viewport만 줄어듦) 대응 — WebKit·Chromium에서 가짜 visualViewport로 확인, 320px 가로 넘침 0
- [x] 팀원 규칙 반영: controller 10개 원칙(좋게만 해석하지 않기, 말한 상황만 다루기, "정해줘"에도 기준 정리, injection 거절 후 철학자 유지), style(길이는 상황별 범위: 짧은 말 80자 이내 / 보통 고민 80~150자 / 복잡한 사연 200자 이내, 열린 질문 하나, 쉬운 설명, 직설/부드러움 맞춤), base(정답 코칭이 아닌 성찰), summary(답변 방식 선호 기억), 아리스토텔레스(신뢰를 깎는 반복 행동은 분명히 짚기)
- [x] mock 답변 단축(평균 약 245자 → 113자): 공감 + 관점 하나 + 질문. 상대의 잘못을 자기 탓으로 돌리는 경우 분기 추가
- [x] 길이를 상황에 맞춘 범위로 변경(사용자 요청: 150자도 길 때가 있음). mock은 20자 이하의 짧은 말에 공감/감사 + 질문만(40~71자), 고민에는 98~128자. 긴 mock 문장 다듬음, 테스트로 범위 고정
- [x] 검증: pytest 215 통과, ui_check 44/44, device_check 65/65, a11y 위반 0, production smoke 27/27 (751c852 배포 후, 인사 문구 순서에 맞춰 smoke 점검 수정)
- [x] 바뀐 prompt로 live 확인 (Sonnet 5.5, 사용자 승인): 팀원 규칙 시나리오 모두 통과. 글자 수 범위만으로는 길이가 안 지켜져 문장 수 기준으로 바꾼 뒤 재확인
- [x] Sonnet 5.5 `between_tools`에서 thinking block만 오고 빈 답변이 되는 문제 → thinking headroom 추가, 같은 질문 3회 정상
- [x] `scripts/live_eval.py`: 평가 사용자 생성(하루 질문 한도), 상황별 길이 기준, T1~T5·L1~L3 시나리오 추가

## API 크레딧 조회 (사용자 요청)
- [x] 조사: API에 잔액 조회 endpoint 없음(공식 문서 확인). Admin API Cost API는 Admin 키 필요 → 키 발급 계정 접근 불가라 사용 불가
- [x] 호출마다 비용 계산·저장: 응답 token × 모델별 공식 단가(`ai/pricing.py`, Opus/Sonnet/Haiku 5.5·Fable 5.1, Haiku 100K 초과 단가), `cost_micro_usd` 정수로 대화 usage·`usage_daily`·누적 `usage_totals/all`(batch)에 기록. 실패 과금·요약 포함
- [x] 대시보드 'API 크레딧': 충전 금액·서비스 밖 사용액 입력(`settings/billing`, admin만, CSRF), 남은 금액·평균 비용·남은 답변 수. 월 예산 환경변수는 제거
- [x] 일별·사용자별 비용 열, 실제 API 모드일 때 모델 단가 표시
- [x] 테스트(가격 계산, 기록, 화면, 입력 검증, 권한) + 실제 Firestore smoke(누적 문서는 임시 key로만)
- [x] 서비스 밖 사용액 추정: 지금까지 live 평가(Sonnet 5.5) 약 $0.28 — 27회 $0.118 + 16회 $0.072 + 16회 $0.063 + 디버그 4회 약 $0.02 (이후 Haiku 비교 16회 $0.004 추가)
- [x] 관리자 대시보드에 API 키 설정 여부 표시(값은 표시하지 않음)

## 사진 첨부 · 휴지통 · 짧은 답변 (사용자 요청)
- [x] 사진 첨부: 고르면 입력창 위에 대기(진행률·빼기), 사진만으로도 전송, 글과 함께 전송, 한 번에 5장, 한 장 100MB, 사진 1장당 질문 1개 추가 차감(입력창 아래 미리 표시)
- [x] 저장: Firestore에 base64 (원본은 1MB 문서 한도 때문에 서버에서 재인코딩: EXIF 제거, 가로 900px, 긴 캡처는 1300px 조각 최대 4개, 조각당 JPEG ≤ 700KB), 표시할 때 디코딩해서 전송(본인만)
- [x] 비용 최적화: Haiku 5.5가 사진을 한 번 글로 옮김(캡처는 나:/상대: 대화록, 사진은 1~2문장 설명) → Sonnet은 글만 읽음. 실패 시 그 사진만 직접 전송. 서버 OCR은 메모리·말풍선 구분 문제로 채택 안 함
- [x] 대화 캡처 요청은 AI가 정말 필요하다고 판단할 때만 한 번(style)
- [x] 답변 더 짧게: "카톡 말풍선 한두 개", 60/120/180자, 한 문장 40자 안팎, 짧은 답 예시 1개. injection 거절 때 이름 말하던 문제 수정
- [x] 휴지통: 휴지통으로 이동 → 되살리기 / 영구 삭제(메시지·사진 포함) / 비우기, 30일 뒤 자동 영구 삭제, 설정·기록에서 링크
- [x] 버그: 새 대화가 사이드바에 새로고침 전까지 안 보이던 문제 → 보내자마자 맨 위에 추가·제목 갱신
- [x] 검증: pytest 267, 브라우저 E2E(Chromium·WebKit) 30/31(WebKit 뷰포트 경고 1개는 앱 오류 아님), ui_check 44/44, device_check 65/65, a11y 0, 로컬 smoke 28/28, live(Haiku 사진 읽기 + Sonnet 답변)
- [x] production(6004120): 사진 업로드·전송·표시·휴지통·영구 삭제 10/10, smoke 29/29(실제 API), 관리자 크레딧 서비스 밖 사용액 $0.40으로 갱신

## UI / UX
- [x] Theme: System / Light / Dark (localStorage, headless 브라우저로 저장·해제 확인)
- [x] Animation (page/message/sheet/dialog/typing/press + prefers-reduced-motion)
- [x] Settings (프로필, 테마, 대화 기록, 로그아웃)
- [x] Desktop sidebar layout (≥960px)
- [x] Mobile 360/375/390/430 검증 (headless Chromium: 가로 넘침·sticky composer·44px·Enter 동작)
- [x] 접근성 (label, focus ring, 44px, role=log, 대비 4.5:1, reduced motion)
- [x] axe-core 자동 접근성 감사 (`scripts/a11y_check.py`: light/dark 26개 화면, 위반 0 — 링크 밑줄, CTA 대비, sheet 키보드 스크롤, heading 순서, landmark 수정)
- [x] PWA manifest(홈 화면 추가, service worker 없음) · robots.txt

## Docs / QA
- [x] README (spec 101 항목 + UI/로그/오류/범위/스크립트, 리뷰 지적 반영)
- [x] Controller scenario 테스트 (A~D mock, E~G memory)
- [x] API 사용량 검증 (1 message = 1 call, 페이지 AI 호출 0 테스트)
- [x] Multi-agent 코드 리뷰 (보안·정확성 리뷰 10건 + spec 준수 감사 20건) → 아래 Review fixes
- [x] Production smoke test (`scripts/smoke_test.py`: 27/27, 실제 Firestore, 테스트 계정 정리)
- [x] Headless browser UI 검증 (`scripts/ui_check.py`: 360/375/390/430, local 44/44)
- [x] Production 재검증 (`9703c49`): smoke 27/27 · headless UI 44/44 · 테스트 계정 정리

## Review fixes
- [x] Rate limiter: 무제한 key 생성(메모리 DoS) · prune window 혼용 · IP 먼저 검사
- [x] Open redirect: `next=/<TAB>/evil.com` 우회 차단 (제어문자 거부 + urlsplit 검사)
- [x] 답변 생성 중 retry/중복 전송 → 대화별 reply lock + 저장 순서 검증 + "답변을 준비하고 있어요" 상태
- [x] 요약을 요청 thread 밖(단일 worker executor)에서 실행, 요약 저장 실패도 다음 단계까지 보류
- [x] 비용 상한: 사용자별 일일 메시지 + 전체 시간당 AI 호출 circuit breaker
- [x] Refusal fallback 기본 off (spec: 메시지당 생성 1회 · 자동 retry 금지)
- [x] 답변 저장 실패 시 재시도 가능 오류로 처리, JS는 저장 전 오류일 때만 입력 복원
- [x] 잘린 요약 출력이 user memory를 지우지 않도록 (닫히지 않은 블록 = 변경 없음)
- [x] 잘못된 입력 500 방지 (비문자 content, 비ASCII CSRF, naive `before`)
- [x] conversation_id UUID 검증, Firestore 예약 username(`__x__`) 거부
- [x] thinking off 모델은 effort를 high 이하로 제한, system prompt에 별도 cache breakpoint
- [x] production에서는 대화별 controller 값과 무관하게 항상 controller on
- [x] SDK debug log가 prompt를 남기지 않도록 anthropic/httpx logger 고정
- [x] 상담 기억(user memory) 보기/지우기 (Settings)
- [x] 대비(text-3, focus ring, danger, 말풍선, chip) · retry 버튼 44px · a11y 세부(chips/typing/menu)
- [x] Mock: 스토킹·강압 표현 안전 분기, controller off 비교가 의미 있게
- [x] 문구: 회원가입/로그인 존댓말 통일, injection 거절 문구, controller 규칙 5 표현, 요약 제외 항목
- [x] README 정확성(300 token 의미, raw 11~20개, 스크립트, UI/로그/오류/범위 섹션)
- [x] Client IP: Render(Cloudflare) `True-Client-IP` 사용, ProxyFix `x_for=3`, `x_host` 미사용 (조사: Render staff 답변·Cloudflare 문서)
- [x] Firestore smoke에 `expect_count` 충돌·memory 삭제 추가 (실제 Firestore 통과)
- [x] UI: 홈 목록 긴 제목 overflow, 다크 모드 버튼 대비, 입력창 44px 터치 영역, 다크 테마 선택 표시

---

## 발견된 이슈 / 메모
- 작업 폴더(Z:)가 RaiDrive WebDAV 마운트라 venv는 로컬 디스크(`C:\Users\User\.venvs\askphilo`)에 둠.
- Render 기본 `GUNICORN_CMD_ARGS`에 `--preload`가 있어 render.yaml에서 override. Firestore client는 어쨌든 lazy 초기화.
- Windows에서는 gunicorn이 실행되지 않음 → local은 Flask dev server, gunicorn은 WSL에서 확인함.
- WebDAV 경로라 git `safe.directory` 예외를 global config에 이 폴더만 추가함.
- commit author는 GitHub noreply 주소 사용 (public repo에 개인 이메일 노출 방지).
- RaiDrive(WebDAV) 마운트가 파일 읽기/쓰기를 간헐적으로 실패시킴(빈 module import, `No such device`, `Permission denied`) → 로컬 검증은 checksum 검증된 로컬 mirror에서 실행, commit 전 staged blob과 파일 내용 대조.
- `/new`, `/c/<id>`는 상담 기능 commit 전까지 "곧 열려요" 화면이었음 → 상담 기능으로 교체 완료.
- render.yaml의 `AI_MODE`/`ANTHROPIC_MODEL`을 한때 `sync: false`(Dashboard 소유)로 두었다가, 운영 전환 때 다시 render.yaml 값(anthropic / claude-sonnet-5-5)으로 고정함. `ANTHROPIC_API_KEY`만 Dashboard 소유.
- Opus 5.5는 thinking을 끌 수 없음 → `max_tokens = MAX_OUTPUT_TOKENS + ANTHROPIC_THINKING_HEADROOM_TOKENS`. Haiku/Sonnet 5.5는 thinking off + 300.
- Raw context는 10~19개(요약되지 않은 메시지 전부, 빠지는 구간 없음). 요약은 메시지 20, 30, 40…에서 실행.
- 가입 후 바로 로그인(Home 이동)으로 흐름을 줄임 — spec의 Signup → Login → Home 대비 의도적 단축 (변경 쉬움).
- 분당·전체 시간당 상한은 in-process(1 worker) 기준이라 재배포·재시작 시 초기화됨. 사용자별 하루 질문 한도는 Firestore(`daily_usage`)에 저장돼 유지됨.
- d6840af 배포가 Render에서 실패(15분 후 failure, 로컬 테스트·production 설정 기동은 정상) → 다음 commit으로 재배포.
- RaiDrive 쓰기 오류로 todo.md가 0바이트로 잘린 적 있음 → HEAD + 변경분으로 복원, 이후 로컬 작성 후 checksum 검증하며 복사.
- 로컬 브라우저 테스트는 5060/5061(SIP) 포트를 쓰면 Chromium·WebKit이 접속을 거부함 → 5000/5070 사용.
- Playwright WebKit 스크린샷이 주입하는 style을 CSP가 차단해 콘솔 오류가 남음 → 검사에서 제외(앱 문제 아님).
- 로컬 UI 점검 스크립트 여러 개를 한 서버에 연달아 돌리면 가입 상한(IP당 시간당 10회)에 걸려 가입이 멈춤 → 스크립트마다 새 서버로 실행.
- Git Bash heredoc을 거치면 `\n` 이스케이프가 실제 줄바꿈으로 바뀌어 Python 패치가 깨짐 → 패치는 파일로 작성해 실행.
