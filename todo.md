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
- [x] password hashing (werkzeug scrypt, 평문 저장/로그 금지 테스트)
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
- [!] 실제 Anthropic 호출 검증 — 사용자가 AI_MODE=anthropic 전환을 결정할 때까지 보류 (유료). Render `ANTHROPIC_MODEL` 현재값 확인 필요

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
- render.yaml의 `AI_MODE`/`ANTHROPIC_MODEL`을 `sync: false`로 변경 → Dashboard 값이 우선. 최초 Blueprint 생성 때 `ANTHROPIC_MODEL=claude-haiku-5-5`가 들어갔으므로 전환 전 Dashboard에서 원하는 모델로 설정 필요.
- Opus 5.5는 thinking을 끌 수 없음 → `max_tokens = MAX_OUTPUT_TOKENS + ANTHROPIC_THINKING_HEADROOM_TOKENS`. Haiku/Sonnet 5.5는 thinking off + 300.
- Raw context는 10~19개(요약되지 않은 메시지 전부, 빠지는 구간 없음). 요약은 메시지 20, 30, 40…에서 실행.
- 가입 후 바로 로그인(Home 이동)으로 흐름을 줄임 — spec의 Signup → Login → Home 대비 의도적 단축 (변경 쉬움).
- 일일/시간당 상한은 in-process(1 worker) 기준이라 재배포·재시작 시 초기화됨.
- RaiDrive 쓰기 오류로 todo.md가 0바이트로 잘린 적 있음 → HEAD + 변경분으로 복원, 이후 로컬 작성 후 checksum 검증하며 복사.
- 로컬 브라우저 테스트는 5060/5061(SIP) 포트를 쓰면 Chromium·WebKit이 접속을 거부함 → 5000/5070 사용.
- Playwright WebKit 스크린샷이 주입하는 style을 CSP가 차단해 콘솔 오류가 남음 → 검사에서 제외(앱 문제 아님).
- Git Bash heredoc을 거치면 `\n` 이스케이프가 실제 줄바꿈으로 바뀌어 Python 패치가 깨짐 → 패치는 파일로 작성해 실행.
