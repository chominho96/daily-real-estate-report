#!/usr/bin/env bash
set -euo pipefail

AUTH_FILE="${1:-$HOME/.codex/auth.json}"
REPO="${2:-}"

if [[ ! -f "${AUTH_FILE}" ]]; then
  echo "auth.json 파일을 찾을 수 없습니다: ${AUTH_FILE}" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI가 필요합니다. https://cli.github.com/ 에서 설치하세요." >&2
  exit 1
fi

python3 - <<'PY' "${AUTH_FILE}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"auth.json 파싱 실패: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(payload, dict):
    print("auth.json 형식이 올바르지 않습니다: JSON object가 필요합니다.", file=sys.stderr)
    raise SystemExit(1)

print("auth.json 형식 검증 완료")
PY

if [[ -n "${REPO}" ]]; then
  gh secret set CODEX_CLI_KEY --app actions --repo "${REPO}" < "${AUTH_FILE}"
  echo "CODEX_CLI_KEY secret 업데이트 완료: ${REPO}"
else
  gh secret set CODEX_CLI_KEY --app actions < "${AUTH_FILE}"
  echo "CODEX_CLI_KEY secret 업데이트 완료: 현재 저장소"
fi
