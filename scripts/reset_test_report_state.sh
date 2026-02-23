#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTS_DIR="${ROOT_DIR}/docs/reports"
INDEX_FILE="${ROOT_DIR}/docs/index.md"
STATE_FILE="${ROOT_DIR}/state/last_run.json"

TZ_OFFSET="${1:-+09:00}"

if [[ ! -d "${REPORTS_DIR}" ]]; then
  echo "reports directory not found: ${REPORTS_DIR}" >&2
  exit 1
fi

REPORT_FILES=()
while IFS= read -r report_file; do
  REPORT_FILES+=("${report_file}")
done < <(find "${REPORTS_DIR}" -maxdepth 1 -type f -name "*.md" ! -name ".gitkeep" -print | sort -r)

if (( ${#REPORT_FILES[@]} == 0 )); then
  echo "no report files found under ${REPORTS_DIR}" >&2
  exit 1
fi

DELETED_REPORT="${REPORT_FILES[0]}"
rm -f "${DELETED_REPORT}"
echo "removed latest report: $(basename "${DELETED_REPORT}")"

REPORT_FILES=()
while IFS= read -r report_file; do
  REPORT_FILES+=("${report_file}")
done < <(find "${REPORTS_DIR}" -maxdepth 1 -type f -name "*.md" ! -name ".gitkeep" -print | sort -r)

if (( ${#REPORT_FILES[@]} == 0 )); then
  echo "no remaining reports to derive last_run_at; state file was not updated" >&2
  exit 1
fi

LATEST_REPORT="${REPORT_FILES[0]}"
HEADER_LINE="$(head -n 1 "${LATEST_REPORT}")"
if [[ ! "${HEADER_LINE}" =~ \(([0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2})\) ]]; then
  echo "failed to parse generated time from ${LATEST_REPORT}" >&2
  exit 1
fi

LAST_RUN_AT_RAW="${BASH_REMATCH[1]}"
LAST_RUN_AT_ISO="${LAST_RUN_AT_RAW/ /T}${TZ_OFFSET}"
printf '{\n  "last_run_at": "%s"\n}\n' "${LAST_RUN_AT_ISO}" > "${STATE_FILE}"
echo "updated last_run_at to ${LAST_RUN_AT_ISO}"

format_label() {
  local stem="$1"
  if [[ "${stem}" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})-([0-9]{2})-([0-9]{2})$ ]]; then
    printf "%s %s:%s" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
    return
  fi
  if [[ "${stem}" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})-([0-9]{4})$ ]]; then
    local hhmm="${BASH_REMATCH[2]}"
    printf "%s %s:%s" "${BASH_REMATCH[1]}" "${hhmm:0:2}" "${hhmm:2:2}"
    return
  fi
  printf "%s" "${stem}"
}

{
  echo "# 일일 부동산 시황 보고서"
  echo
  echo "매일 자동 생성되는 부동산 시황 보고서입니다."
  echo
  echo "## 최신 보고서"
  echo
  for report_path in "${REPORT_FILES[@]}"; do
    report_name="$(basename "${report_path}")"
    report_stem="${report_name%.md}"
    label="$(format_label "${report_stem}")"
    echo "- [${label}](reports/${report_name})"
  done
  echo
  echo "## 지역 설정 방법"
  echo
  echo "\`config/regions.yaml\` 수정 후 커밋하면 다음 실행부터 반영됩니다."
} > "${INDEX_FILE}"

echo "rewrote docs/index.md"
