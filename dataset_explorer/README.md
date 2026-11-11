# 기준 데이터 탐색실

Step1·Step2와 독립된 로컬 전용 UI입니다. 제공받은 기준 분할 Dev/Test/Holdout의 구성과 원문·정답 라벨을 확인합니다. 추가 실험용 사례·review queue·검색 질의·검색 Gold는 대상에 포함하지 않습니다. 이 화면의 수치는 원본 annotation 집계이며 모델 성능이 아닙니다.

저장소 루트에서 실행하세요. Python 3.10 이상이면 되며 Conda·추가 패키지·모델·DB·API 키가 필요하지 않습니다.

```bash
python3 dataset_explorer/server.py
```

브라우저: **http://127.0.0.1:8770**. 종료는 실행 중인 터미널에서 `Ctrl+C`입니다.

기본 입력은 기존 비공개 `step2_privacy_rag/local_eval/`의 아래 세 파일뿐입니다.

| 분할 | 파일 |
|---|---|
| Dev | `pii_synth_finance_dev.jsonl` |
| Test | `pii_synth_finance_test.jsonl` |
| Holdout | `holdout_labeled.jsonl` |

다른 안전한 로컬 디렉터리에 동일 파일들이 있으면 다음처럼 지정합니다.

```bash
python3 dataset_explorer/server.py --data-dir /safe/path/to/corpus --port 8770
```

- **전체 구성:** 문서·개체 수, PII 라벨별 구간/포함 문서 수, 문서 길이, 문서 유형, 어려운 사례 태그 비교.
- **문서 탐색:** 분할·유형·PII 라벨·사례 태그·annotation 상태 필터, 원문 검색, 길이/개체 수 정렬, 원문 개체 강조, 개체별 오프셋·form·person_id, 원본 JSON.
- **스키마·품질:** 필드 존재 여부, annotation 오프셋/텍스트 불일치, 겹치는 구간, 개체 없는 문서, 동일 원문 중복, 파일 SHA256.

원본을 읽기만 하며 데이터 복사본·분석 JSON·HTML 내장 데이터 파일을 생성하지 않습니다. 원본 변경을 반영하려면 서버를 재시작합니다. 브라우저 메모리에서 데이터를 표시하고 localStorage·쿠키·외부 CDN·외부 API를 사용하지 않습니다. 서버는 `127.0.0.1`에만 바인딩하고 요청 로그를 기록하지 않습니다. UI 코드만 공유할 수 있고 원본 데이터는 계속 Git에서 제외됩니다. 이 도구의 Python 서버 대신 프로젝트 전체를 단순 정적 서버로 노출하지 마세요.

Dev/Test에는 `template`, `persons`, `roles`가 있고 Holdout에는 제공되지 않습니다. 해당 필드 부재를 개체 라벨 누락으로 간주하지 않습니다. 오프셋은 Python Unicode 문자 기준 `[start, end)`이며, 브라우저에서도 코드 포인트 기준으로 강조합니다. 구간 겹침은 관찰 항목이며 그 자체를 오류로 판정하지 않습니다. 중복은 공백·줄바꿈까지 동일한 원문만 비교합니다.

소스와 UI 계약 테스트(합성 데이터만 사용):

```bash
python3 -m unittest discover -s dataset_explorer -p 'test_*.py' -v
```
