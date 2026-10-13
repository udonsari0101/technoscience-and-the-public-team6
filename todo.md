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
- [x] `render.yaml` (free · singapore · main · /health · AI_MODE=mock)
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
- [x] Loading: typing dots, input 비활성화
- [x] 실패 시 사용자 message 보존 + 명시적 Retry
- [x] Conversation 다시 열기 (+ 이전 메시지 더 보기)
- [x] History 목록 (+ 더 보기)
- [x] Conversation 삭제 (messages subcollection 포함, 확인 dialog/페이지)
- [x] Soft limit 안내 / Hard limit 차단 (기존 대화는 읽기 가능)
- [x] 대화별 접근 제어 (다른 사용자 404)
- [ ] Mobile chat keyboard 실제 기기 테스트 (iOS Safari / Android Chrome)

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
- [x] 기본 모델 `claude-opus-5-5`, `ANTHROPIC_EFFORT=low`, thinking headroom
- [!] 실제 Anthropic 호출 검증 — 사용자가 AI_MODE=anthropic 전환을 결정할 때까지 보류 (유료). Render `ANTHROPIC_MODEL` 현재값 확인 필요

## UI / UX
- [~] Theme: System / Light / Dark (localStorage) — 구현, 브라우저 확인 대기
- [x] Animation (page/message/sheet/dialog/typing/press + prefers-reduced-motion)
- [x] Settings (프로필, 테마, 대화 기록, 로그아웃)
- [x] Desktop sidebar layout (≥960px)
- [ ] Mobile 360/375/390/430 검증 (headless browser screenshot)
- [~] 접근성 (label, focus, 44px, role=log, aria) — 구현, 점검 대기

## Docs / QA
- [x] README (spec 101 항목 전체)
- [x] Controller scenario 테스트 (A~D mock, E~G memory)
- [x] API 사용량 검증 (1 message = 1 call, 페이지 AI 호출 0 테스트)
- [ ] Multi-agent 코드 리뷰 + 검증 (security / correctness / spec compliance / cost)
- [ ] Production smoke test

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
