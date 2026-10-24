# AskPhilo

**철학자와 나누는 연애 상담**

플라톤, 아리스토텔레스, 에피쿠로스, 니체의 관점으로 연애 고민을 새롭게 바라보는 AI 대화 서비스입니다.
정답이나 결정을 대신하기보다, 자신의 마음과 관계를 돌아보고 스스로 판단할 수 있도록 질문을 건넵니다.

[서비스 이용하기](https://askphilo.kro.kr)

## 함께 이야기할 철학자

| 철학자 | 함께 살펴볼 고민 |
| --- | --- |
| 플라톤 | 사랑에서 진정으로 바라는 것과 관계의 가치 |
| 아리스토텔레스 | 좋은 관계를 만드는 습관과 반복되는 행동 |
| 에피쿠로스 | 진짜 원하는 것과 불안에서 비롯된 욕구 |
| 니체 | 타인의 기준을 벗어나 솔직하게 마주하는 나의 마음 |

## 주요 기능

- 텍스트와 사진을 함께 보내는 상담
- 짧은 말풍선으로 이어지는 대화와 답변의 철학적 배경 확인
- 대화 기록 보관, 휴지통 이동 및 복원
- 라이트·다크 테마와 답변 표시 속도 설정

## 로컬 실행

Python 3.14 기준입니다.

```bash
python -m pip install -r requirements.txt
python wsgi.py
```

[http://localhost:5000](http://localhost:5000)에서 확인할 수 있습니다. 별도 환경 설정이 없으면 모의 AI 응답과 메모리 저장소로 실행되며, 데이터는 재시작 시 초기화됩니다.
실제 AI와 Firestore를 연결하려면 [.env.example](.env.example)을 참고해 `.env`를 설정합니다.

**기술 구성:** Flask · JavaScript · Anthropic API · Cloud Firestore · Render

UNIST 「Technoscience and the Public」 Team 6 프로젝트
