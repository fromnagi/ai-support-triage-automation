# 검증 기록

2026-10-02, 합성 데이터와 loopback 서비스로 확인했습니다. Python 3.12.10, Node.js 22.18.0, npm 10.9.3, n8n 2.20.12를 사용합니다. 외부 CRM과 실제 고객 발송은 사용하지 않습니다.

## 수정 전 재현

[원래 워크플로 재현 결과](demo/reproduction.json)는 별도 n8n 2.20.0 데이터 폴더에서 수집했습니다.

- 등록 API가 추가 필드를 반환하거나 분류 API가 잘못된 상태를 반환하면 웹훅은 HTTP 200과 빈 본문을 반환했습니다. n8n 실행 DB에는 계약 오류가 기록됐습니다.
- 본문 끝의 U+001C는 JavaScript에서 유지되고 Python에서 제거됐습니다. 등록 후 응답 비교가 실패하여 `RECEIVED`에 남고 재전송도 빈 성공 응답이었습니다.
- 등록 직후 및 분류 직후의 저장 상태를 만들고 재전송했을 때, HTTP 200의 `duplicate`에는 `RECEIVED` / `AI_CLASSIFIED`가 표시되지 않았습니다. 실제 프로세스를 강제 종료한 검사는 아닙니다.
- 시도 혼합은 한 번의 Store 실행 순서로 재현했습니다. 이전 분류 → 쓰기 실패 확인 → 새 등록과 `technical` 분류 → 늦은 이전 쓰기 → 새 쓰기 순서에서 저장 분류는 새 값이지만 CRM에는 `OLD DRAFT`가 남았습니다.

## 정책과 출력 검증

Python 단위 검사, JavaScript 정책·응답 계약 검사, 워크플로 및 오프라인 예시 재생성 검사, Python 컴파일 검사, 공개 문서 링크 검사를 실행합니다. 회귀 검사는 ECMAScript 공백, UTF-16 길이, 이메일 대소문자 정규화, 재시작 후 중복 상태, 누락·다른 시도 번호, 이전 기록의 알 수 없는 시도 번호를 포함합니다.

의도적으로 환불 문장에 유효한 `order_status` 모델 출력을 넣는 검사에서는 `AUTO`가 허용됩니다. 이것이 의미상 고위험 요청을 모두 식별한다는 주장을 제거한 이유입니다. `refund` / `account_access` 모델 라벨을 받았을 때 사람 검토로 보낸다는 정책을 검사합니다. 가짜 AI의 제목 키워드 선택은 주입 공격 방어 증거로 사용하지 않습니다.

## 실제 n8n 웹훅 결과

최종 워크플로를 기존 저장소와 새 Git 체크아웃 각각의 전용 n8n/SQLite 폴더에 가져와 실행했습니다. 기본, 확장, 동시 요청, 실패·정규화·중간 상태·시도 번호 회귀 검사가 두 환경에서 완료됐습니다. [실제 응답과 저장 상태 발췌](demo/live-results.json)를 제공합니다.

| 요청 | 실제 결과 |
| --- | --- |
| 주문 조회 / 모델 라벨 환불 / 잘못된 AI JSON | CRM 초안 / 사람 검토 / 사람 검토 |
| 동시에 같은 문의 12회 | 한 번 처리, 중복 11회, CRM 기록 한 개 |
| 등록·분류 API 400/503, 빈 응답, 204, 잘못된 계약 | HTTP 503 및 `PROCESSING_UNRESOLVED`, 사업 기록 없음 |
| `RECEIVED` / `AI_CLASSIFIED` / `AI_FAILED` 재전송 | HTTP 202, 실제 상태와 `needs_attention: true` |
| 정상 기록 후 응답 유실 / 잘못된 응답 시도 번호 | 상태 API가 실제 기록 확인 후 HTTP 200 완료 |
| 사업 API 연결 거부 | HTTP 502 `WRITE_FAILED`, 기록 없음 |
| 상태 확인 연결 거부 / 서비스 중단 | HTTP 503 `WRITE_UNRESOLVED` |
| AI 연결 거부 / HTTP 시간 초과 | 사람 검토와 해당 오류 사유 |
| 이전 쓰기 요청을 보류한 동안 새 시도 완료 | 이전 쓰기는 `INVALID_STATE`로 거절, 분류 `technical`과 `NEW DRAFT` 모두 시도 1 |
| U+001C / BOM / Unicode 이메일 / 이모지 길이 경계 | 정상 저장 및 완료 중복 응답 |

최종 Python 12개 검사가 통과했습니다. JavaScript 28개 정책 사례와 입력·등록·분류·사업 응답·상태 확인 계약 검사가 통과했습니다. `build_workflow.py --check`, `build_demo.py --check`, `check_public.py`, Python 컴파일 및 JavaScript 구문 검사를 실행했습니다. 새 체크아웃에서 Python/JS 및 재생성·문서 검사도 다시 실행했습니다. 이전 버전의 실제 모델 관찰은 이번 결과로 재사용하지 않았습니다.

새 체크아웃은 기준 Git 이력에서 복제한 뒤 수정된 소스와 새 파일을 적용했습니다. `node_modules`, n8n 데이터, 사업 DB, 자격 증명을 복사하지 않았습니다. 최종 잠금 파일로 `npm ci`가 2,116개 패키지를 설치했고 실제 CLI는 2.20.12를 보고했습니다. 시작 도중 DB 준비 503과 JS runner offer 만료 경고를 관찰했습니다. 웹훅 준비 뒤 검사는 통과했지만 이 관찰로 무중단 운영이나 시작 속도를 보장하지 않습니다.

## 의존성 검토

`npm ci`를 기존 저장소와 복사된 런타임 없이 만든 Git 체크아웃에서 실행했습니다. 초기 잠금 파일의 빈 `@types/*` 항목 12개는 npm의 `unrecognized node in tree` 경로와 일치했습니다. 잠금 파일을 새로 만들었고 최종 파일에는 빈 패키지 항목이 없습니다. 최종 새 체크아웃 설치는 종료 코드 0이며 같은 잠금 손상 경고가 없었습니다. 지원 종료 패키지 경고는 별도이며 남아 있습니다.

직접 의존성은 n8n 하나입니다. 사용하지 않는 루트 핀을 제거했습니다. 남은 overrides에는 각각 이유가 있습니다.

| 항목 | 이유 |
| --- | --- |
| n8n 2.20.12 | 2.20 계열 패치. [HTTP Request 페이지네이션 취약점](https://github.com/n8n-io/n8n/security/advisories/GHSA-c8xv-5998-g76h) 등 2.20.7에 고쳐진 문제의 영향 범위를 벗어남 |
| @langchain/core 1.2.8 | n8n 시작이 참조하는 `utils/uuid` 내보내기를 유지. 자동 선택된 1.1.41은 CLI 시작을 깨뜨렸음 |
| vm2 3.12.2 | 기존 잠금 파일의 패치 버전을 유지. n8n 패치의 고정 의존성이 3.11.2로 되돌아가는 것을 방지 |
| form-data 4.0.6 | [multipart 필드/파일명 주입 수정](https://github.com/advisories/GHSA-hmw2-7cc7-3qxx). 이 워크플로의 요청은 JSON이지만 의존성 트리의 이전 패치 수준도 보존 |

최종 `npm audit --json`: 8 low, 96 moderate, 38 high, 2 critical (total 144). 이는 취약 패키지 집계이며 이 워크플로에서 같은 수의 공격이 가능하다는 의미가 아닙니다. `npm audit fix --force`는 실행하지 않았습니다.

| 남은 위험 | 이 흐름에서의 접근 가능성 및 한계 |
| --- | --- |
| 공개 웹훅 내부 복사의 [prototype pollution](https://github.com/n8n-io/n8n/security/advisories/GHSA-2vff-hj5x-8gq7), 수정 2.25.7 / 2.26.2 | Webhook → 변환 → HTTP Request 경로를 사용함. 입력 검사는 n8n 내부 복사 이후이며 취약점의 수정이라고 볼 수 없음. URL은 고정되고 자격 증명이 없지만 HTTP helper가 Axios를 실제로 호출하므로 라이브러리 위험을 무시하지 않음. 공개 노출은 허용하지 않음 |
| 표현식·JS 실행 관련 권고 | 고정 Code/표현식이 실행되므로 해당 기능은 사용 중. 고객 문자열을 실행 코드로 평가하지 않고 워크플로 편집 권한을 외부에 제공하지 않음. 샌드박스 안전성 전체를 검증하지 않음 |
| [OAuth 등록 저장소 고갈](https://github.com/n8n-io/n8n/security/advisories/GHSA-hh89-3r9w-qj3j), 수정 2.37.7 / 2.38.2 | 노드 목록만으로 제외할 수 없는 인스턴스 경로. 설치 소스의 `/mcp-oauth/register`는 MCP enabled guard를 가짐. 두 검증 DB의 `mcp.access.enabled`는 미등록이며 소스의 `getEnabled`는 이 경우 false를 반환함. 활성화하거나 공개 노출하면 다시 평가해야 함. 큰 등록 요청으로 공격 검사는 하지 않음 |
| Respond to Webhook XSS | JSON 응답만 사용하고 binary 응답과 공격자가 정한 Content-Type을 사용하지 않음 |
| critical: fast-xml-parser, tar | XML·Git·Zep·압축 해제 노드는 없음. tar는 설치/패키지 취급 경로에 남으며 고객 문의의 처리 경로에서 임의 압축 파일을 풀지 않음. 해당 라이브러리 전체를 안전하다고 주장하지 않음 |

2.20.12는 현재 권고 전체를 해소하지 않습니다. 이후 n8n 계열로의 넓은 이동은 이 localhost 포트폴리오 수리에서 진행하지 않았습니다. 공개 서버 운영에는 지원 버전 선정, 새 검증, 인증과 접근 제어 검토가 필요합니다.

## 실행 증거의 구분

[오프라인 예시](demo/offline-scenarios.json)는 Store와 정책 코드를 실행한 합성 결과입니다. n8n 결과와 구분합니다. 생성 그림은 런타임 화면으로 제시하지 않습니다. 브라우저 도구가 localhost 탐색을 차단하여 실제 화면 캡처는 추가하지 못했습니다.

이번 검증에서 실제 Ollama 모델을 다시 호출하지 않았습니다. 모델 정확도, 프롬프트 주입 저항성, 외부 CRM의 idempotency, 운영 서비스의 안정성은 검증 범위에 포함하지 않습니다.
