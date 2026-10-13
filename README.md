# AskPhilo

UNIST — Technoscience and the Public, Team 6

원하는 철학자를 선택하고, 그 철학자의 관점과 사고방식으로 연애 고민을 새롭게 바라보는 모바일 친화적 웹 서비스입니다.
AskPhilo는 사용자 대신 결정을 내리지 않습니다. 철학자의 관점으로 고민을 다시 보고, 사용자가 스스로 판단할 수 있도록 돕습니다.

- Production: https://askphilo.kro.kr
- Health check: https://askphilo.kro.kr/health
- 작업 진행 상태: [todo.md](todo.md)

> 이 README는 기능이 추가될 때마다 함께 갱신됩니다.

## Local run

```bash
python -m venv .venv            # (Z: 같은 네트워크 드라이브라면 로컬 디스크에 생성 권장)
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # 값 채우기 (SECRET_KEY 등)
python wsgi.py                  # http://localhost:5000
pytest -q
```

## Secret 관리 (중요 — 이 repository는 PUBLIC)

Git에 절대 올리지 않습니다: `.env`, `secrets/`, Firebase service account JSON, Anthropic API key, private key, `SECRET_KEY`.

- Local: Firebase JSON은 `secrets/firebase-service-account.json` (git-ignored)
- Render: Secret File `firebase-service-account.json` → `/etc/secrets/firebase-service-account.json`
- commit 전 검사: `python scripts/check_secrets.py` (staged) / `--all` (tracked 전체)
- pre-commit hook 설치: `python scripts/check_secrets.py --install-hook`

## Render deployment

`render.yaml` Blueprint가 Python Free web service(`askphilo`, region singapore, branch `main`)를 정의합니다.
`main`에 push하면 자동 배포됩니다(`autoDeployTrigger: commit`).

Render에서 직접 해야 하는 작업:
1. **New → Blueprint**로 이 repository 연결
2. 생성 시 `ANTHROPIC_API_KEY` 입력 (지금은 `AI_MODE=mock`이라 호출되지 않음)
3. **Environment → Secret Files**에 `firebase-service-account.json` 업로드
4. **Settings → Custom Domains**에서 `askphilo.kro.kr` 확인 후 DNS에 CNAME(`<service>.onrender.com`) 등록

## /health

`GET /health` → `{"status":"ok","app":"AskPhilo","firebase_configured":true,"ai_mode":"mock"}`
Firestore 조회, AI 호출, secret 노출이 없는 가벼운 endpoint입니다.
