# daily-real-estate-report

대한민국 부동산 일일 시황 리포트를 자동 생성하고 GitHub Pages로 배포하는 프로젝트입니다.

## 개요

- 리포트 구조는 고정 템플릿이며 섹션 본문만 생성합니다.
- 정책 뉴스, 가격 추이, 기타 인사이트 3개 섹션을 매일 생성합니다.
- 실거래 데이터는 공공 API를 직접 호출합니다.
- LLM 생성은 OpenAI SDK 직접 호출이 아니라 Codex CLI(`codex exec`)를 사용합니다.
- 결과 문서는 MkDocs로 빌드되어 GitHub Pages에 배포됩니다.

## 워크플로우 요약

대상 파일: `.github/workflows/daily-report.yml`

1. `Checkout`으로 저장소를 가져옵니다.
2. Python/Node 환경을 준비하고 Codex CLI를 설치합니다.
3. `CODEX_CLI_KEY`(auth.json 문자열)를 `$CODEX_HOME/auth.json`으로 복원합니다.
4. `daily-report --root .` 실행으로 리포트를 생성합니다.
5. 변경된 리포트/인덱스/상태 파일을 커밋 & 푸시합니다.
6. MkDocs 빌드 후 Pages 아티팩트를 업로드합니다.
7. 기본 브랜치에서 실행된 경우 GitHub Pages 배포와 Discord 알림을 수행합니다.

## 워크플로우 사용 방법

### 1) 필수 준비

1. GitHub Pages Source를 `GitHub Actions`로 설정합니다.
2. Repository Secrets를 추가합니다.
3. Codex CLI 인증 정보(`CODEX_CLI_KEY`)를 최신 상태로 유지합니다.

필수 Secret 목록:

1. `CODEX_CLI_KEY` (`~/.codex/auth.json` 파일 전체 JSON 문자열)
2. `MOLIT_API_SERVICE_KEY`
3. `NAVER_NEWS_CLIENT_ID`
4. `NAVER_NEWS_CLIENT_SECRET`

권장 Variable 목록:

1. `LLM_STRICT=true` (LLM 실패 시 워크플로우 실패)
2. `LLM_CODEX_TIMEOUT_SEC=180`
3. `LLM_DEBUG=true` (초기 점검 시)
4. `REAL_ESTATE_API_STRICT=true`
5. `REAL_ESTATE_API_DEBUG=true` (초기 점검 시)

### 2) 수동 테스트 실행

1. 변경 브랜치를 원격에 푸시합니다.
2. GitHub Actions 탭에서 `Daily Real-Estate Report` 워크플로우를 수동 실행(`workflow_dispatch`)합니다.
3. `generate-and-build` 로그에서 `[LLM] section=... llm=success` 여부를 확인합니다.

참고: 현재 워크플로우 트리거는 `schedule` + `workflow_dispatch`이며, 단순 `push`로는 자동 실행되지 않습니다.

## auth.json 갱신 가이드

로컬에서 auth.json을 얻는 방법:

```bash
codex
cat ~/.codex/auth.json
```

수동으로 Secret 갱신:

1. GitHub Repository > Settings > Secrets and variables > Actions
2. `CODEX_CLI_KEY` 편집
3. `~/.codex/auth.json` 전체 내용을 그대로 붙여넣기

자동 갱신 스크립트:

```bash
./scripts/update_codex_cli_key_secret.sh
```

옵션:

1. `./scripts/update_codex_cli_key_secret.sh /path/to/auth.json`
2. `./scripts/update_codex_cli_key_secret.sh ~/.codex/auth.json owner/repo`

요구사항:

1. GitHub CLI(`gh`) 설치
2. `gh auth login` 완료

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install .[dev]
daily-report --root .
```

## 참고 파일

1. 리포트 설정: `config/report.yaml`
2. 지역 설정: `config/regions.yaml`
3. 상태 파일: `state/last_run.json`
4. LLM 생성기: `src/daily_report/llm/section_writer.py`
