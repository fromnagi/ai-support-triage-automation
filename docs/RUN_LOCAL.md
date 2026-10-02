# 로컬 실행

Python 3.12, Node.js 22, npm 10을 사용합니다. 각 명령은 이 저장소의 PowerShell 창에서 실행합니다. 별도 계정이나 CRM 자격 증명은 사용하지 않습니다.

첫 창에서 설치와 정적 검사를 실행한 뒤 모의 API를 유지합니다. `npm ci`는 인터넷에서 의존성을 다운로드하고 `node_modules`를 씁니다. 나머지는 로컬 계산과 합성 데이터만 사용하며 유료 AI 호출이 없습니다. 정상 종료하면 다음 창을 여세요. 중단되면 로그와 티켓 상태를 먼저 확인합니다.

```powershell
Set-Location -LiteralPath 'C:\dev\portfolio-public\04-ai-support-triage-automation'
npm ci
python -m unittest discover -s tests -v
node tests/policy_cases.cjs
python scripts/build_workflow.py --check
python scripts/build_demo.py --check
python scripts/check_public.py
python demo/fake_business_system.py --db .local/demo.db
```

둘째 창에서 새 전용 데이터 폴더로 워크플로를 가져오고 실행합니다. CLI의 `publish`는 로컬 워크플로 활성화입니다. 외부 배포가 아닙니다. n8n은 `.local`에 실행 기록을 쓰고 loopback에서만 듣습니다. 기존 n8n을 공유하는 포트나 데이터 폴더는 사용하지 마세요.

```powershell
Set-Location -LiteralPath 'C:\dev\portfolio-public\04-ai-support-triage-automation'
$env:N8N_USER_FOLDER='C:\dev\portfolio-public\04-ai-support-triage-automation\.local\n8n-demo'
$env:N8N_LISTEN_ADDRESS='127.0.0.1'
$env:N8N_PORT='5678'
$env:N8N_RUNNERS_BROKER_PORT='5679'
$env:N8N_DIAGNOSTICS_ENABLED='false'
$env:N8N_VERSION_NOTIFICATIONS_ENABLED='false'
$env:N8N_TEMPLATES_ENABLED='false'
$env:N8N_PUBLIC_API_DISABLED='true'
node node_modules/n8n/bin/n8n import:workflow --input=workflows/support-triage.json
node node_modules/n8n/bin/n8n publish:workflow --id=SupportTriageDemo
node node_modules/n8n/bin/n8n start
```

셋째 창에서 합성 요청으로 확인합니다. 모의 API와 n8n이 기록을 저장합니다. 재실행은 새 prefix로 하세요. 정상 종료 후 결과를 읽고 두 서버는 각 창에서 Ctrl+C로 종료합니다. 중단 시 같은 티켓을 무작정 다시 보내지 말고 상태를 조회합니다.

```powershell
Set-Location -LiteralPath 'C:\dev\portfolio-public\04-ai-support-triage-automation'
python demo/local_e2e.py --prefix SAMPLE-A
python demo/extended_e2e.py --prefix SAMPLE-B
python demo/concurrent_e2e.py --ticket-id SAMPLE-C
```

모든 회귀 시나리오를 별도 n8n과 임시 DB로 실행하려면 아래 명령을 사용합니다. 전용 포트를 사용하고 실행을 끝내면 자신이 시작한 서버만 종료합니다. `.local/live-check`의 로그와 결과가 남습니다. 실제 CRM이나 외부 모델에는 연결하지 않습니다.

```powershell
Set-Location -LiteralPath 'C:\dev\portfolio-public\04-ai-support-triage-automation'
python scripts/live_validation.py --directory .local/live-check
```

## 응답과 후속 조치

| HTTP | 상태 | 후속 조치 |
| --- | --- | --- |
| 200 | `AUTO_ROUTED` / `HUMAN_REVIEW`, 또는 완료된 `duplicate` | 초안 또는 검토 항목 확인 |
| 202 | `duplicate`와 중간 상태, `needs_attention: true` | 처리 중 또는 중단됨. 상태를 다시 조회하고 실행 원인 확인 |
| 400 | `INVALID_INPUT` | 입력 수정 |
| 409 | `conflict` | 같은 문의 번호에 다른 내용이 제출됨. 원본 확인 |
| 502 | `WRITE_FAILED` | 모의 API에 기록 없음이 확인됨. 목적지 복구 후 같은 내용 재시도 가능 |
| 503 | `PROCESSING_UNRESOLVED` / `WRITE_UNRESOLVED` | 결과 불명. 상태와 실제 기록 확인 후 조치 |

상태 조회: `GET http://127.0.0.1:8765/state/tickets/<ticket_id>`. `RECEIVED`, `AI_CLASSIFIED`, `AI_FAILED`에는 자동 복구가 없습니다. 조회는 진행 중과 영구 중단을 판별하지 않습니다. 실행 로그를 함께 확인해야 합니다.

**timeout은 실패가 아니라 결과 불명일 수 있습니다.** SQLite 모의 시스템은 기록 저장과 최종 상태를 같은 트랜잭션으로 처리합니다. 실제 CRM은 같은 작업 식별자나 외부 조회를 별도로 설계해야 합니다.

실제 로컬 모델을 쓰려면 이미 설치된 Ollama의 `/api/chat` 주소로 `scripts/build_workflow.py` 변형을 만들 수 있습니다. 로컬 연산 비용이 들며, 기본 검증은 고정 응답의 가짜 AI만 사용합니다. 이 작업의 회귀 검증은 실제 모델의 정확도를 평가하지 않습니다.

이전 모의 DB 기록에는 시도 번호 근거가 없습니다. 번호를 알 수 없는 상태로 보존하고 확인을 요구합니다. 재현에는 새 DB를 사용하세요.
