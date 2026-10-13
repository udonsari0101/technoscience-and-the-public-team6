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
- [ ] Render Blueprint 연결 (사용자 작업)
- [ ] Render Secret File 업로드 (사용자 작업)
- [ ] askphilo.kro.kr DNS + custom domain 검증 (사용자 작업)
- [ ] production `/health` 확인

## Firebase / Firestore
- [ ] Firebase Admin SDK lazy 초기화 (worker 안에서, gRPC fork 안전)
- [ ] Repository interface + Firestore 구현 + In-memory 구현(테스트/로컬)
- [ ] Firestore 연결 local 검증 (테스트용 user 생성 후 정리)

## Authentication
- [ ] 회원가입 UI (이름 · 아이디 · 비밀번호 · 비밀번호 확인)
- [ ] username 규칙 (3~20자, 영문/숫자/_, 대소문자 무시)
- [ ] password hashing (평문 저장/로그 금지)
- [ ] `username_index` transaction으로 중복 가입 방지
- [ ] 중복 username 동시 가입 테스트
- [ ] 로그인 / 로그아웃 (HttpOnly session cookie)
- [ ] 로그인 필요 페이지 보호 (Home/Chat/History/Settings)
- [ ] CSRF 보호

## Home / Philosopher
- [ ] Home (인사, 새 상담 CTA, 최근 대화 5개)
- [ ] 철학자 데이터 (플라톤 · 아리스토텔레스 · 에피쿠로스 · 니체)
- [ ] 철학자 선택 카드 UI ("누구와 이야기해볼까요?")
- [ ] 철학자 상세 (보는 방식 · 잘 맞는 고민 · 질문 스타일)
- [ ] 철학자 original SVG visual (일관된 스타일)

## Conversation / Chat
- [ ] Conversation 생성 (AI 호출 없음)
- [ ] Message 저장 (message_index transaction)
- [ ] Title: 첫 user message 앞부분 (AI 호출 없음)
- [ ] Chat UI (bubble, auto-resize textarea, sticky composer, safe-area)
- [ ] Enter 전송 / Shift+Enter 줄바꿈 (desktop)
- [ ] Loading: typing dots, input 비활성화
- [ ] 실패 시 사용자 message 보존 + 명시적 Retry
- [ ] Conversation 다시 열기
- [ ] History 목록
- [ ] Conversation 삭제 (messages subcollection 포함)
- [ ] Soft limit 안내 / Hard limit 차단

## Memory / Context
- [ ] Context builder (system + user memory + summary + recent messages)
- [ ] Conversation summary (threshold 기반, 실패해도 대화 지속)
- [ ] Summary optimistic concurrency (중복 갱신 방지)
- [ ] Mock summary (deterministic)
- [ ] User-level memory (최소, summary 갱신 시에만)
- [ ] 새 conversation은 다른 대화 raw message 미포함

## AI
- [ ] AI provider interface
- [ ] Mock AI (철학자별 realistic 응답, pytest delay 0)
- [ ] Prompt: base + controller + philosopher + style (간결)
- [ ] controller_enabled true/false 비교 구조 (UI 비노출)
- [ ] Anthropic provider (1 message = 1 call, 자동 retry 없음)
- [ ] Token usage 저장 (input/output)
- [ ] Prompt caching 검토
- [ ] Usage 확인 도구 (Flask CLI / dev route)

## UI / UX
- [ ] Theme: System / Light / Dark (localStorage)
- [ ] Animation (+ prefers-reduced-motion)
- [ ] Settings (프로필, 테마, 대화 기록, 로그아웃)
- [ ] Desktop sidebar layout
- [ ] Mobile 360/375/390/430 검증
- [ ] 접근성 (label, focus, 44px, contrast)

## Docs / QA
- [~] README
- [ ] Controller scenario 테스트 (A~G)
- [ ] API 사용량 검증 (1 message = 1 call)
- [ ] Production smoke test

---

## 발견된 이슈 / 메모
- 작업 폴더(Z:)가 RaiDrive WebDAV 마운트라 venv는 로컬 디스크(`C:\Users\User\.venvs\askphilo`)에 둠.
- Render 기본 `GUNICORN_CMD_ARGS`에 `--preload`가 있어 render.yaml에서 override. Firestore client는 어쨌든 lazy 초기화.
- Windows에서는 gunicorn이 실행되지 않음 → local은 Flask dev server, gunicorn은 WSL에서 확인함.
- WebDAV 경로라 git `safe.directory` 예외를 global config에 이 폴더만 추가함.
- commit author는 GitHub noreply 주소 사용 (public repo에 개인 이메일 노출 방지).
