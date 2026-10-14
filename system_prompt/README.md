# system_prompt

AskPhilo가 Anthropic API에 보내는 system prompt 원문입니다. 앱은 시작할 때 이 파일들을 읽습니다.
파일을 고쳐 `main`에 push하면 다음 배포부터 반영됩니다.

| 파일 | 쓰임 |
|---|---|
| `base.md` | 모든 상담의 기본 역할과 흐름 |
| `controller_v1.md` | 지켜야 할 원칙 (`CONTROLLER_VERSION=v1`). `controller_v2.md`를 만들면 `CONTROLLER_VERSION=v2`로 바꿔 비교할 수 있습니다 |
| `style.md` | 말투와 답변 길이 |
| `philosophers_prompt/<id>.md` | 선택한 철학자의 사고방식 (`plato`, `aristotle`, `epicurus`, `nietzsche`) |
| `summary.md` | 긴 대화를 압축할 때 쓰는 요약 지시 (드물게 실행) |
| `title.md` | 첫 답변과 함께 대화 제목을 받을 때 덧붙이는 한 줄 지시 |

매 요청의 system prompt = `base.md` + `controller_v1.md` + `philosophers_prompt/<id>.md` + `style.md`

- `<!-- ... -->` 주석은 모델에 보내지 않으므로 메모용으로 자유롭게 쓸 수 있습니다.
- 이 파일들은 매 요청마다 반복해서 보내므로(prompt caching 적용) 짧고 분명하게 유지하는 것이 좋습니다.
- 이 폴더의 README.md는 모델에 보내지 않습니다.
