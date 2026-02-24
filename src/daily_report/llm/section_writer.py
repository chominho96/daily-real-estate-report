from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from daily_report.models import LLMConfig, SectionConfig


class SectionWriter:
    def __init__(self, llm_config: LLMConfig, language: str) -> None:
        self._llm_config = llm_config
        self._language = language
        self._debug = os.getenv("LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._strict = os.getenv("LLM_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}
        self._codex_bin = os.getenv("CODEX_CLI_BIN", "codex").strip() or "codex"
        self._codex_timeout_sec = int(os.getenv("LLM_CODEX_TIMEOUT_SEC", "180"))
        self._section_health: dict[str, dict[str, str]] = {}
        self._last_outcome_code = "unknown"
        self._last_outcome_detail = ""
        self._last_outcome_cause = ""

    def _set_outcome(self, code: str, detail: str = "", cause: str = "") -> None:
        self._last_outcome_code = code
        self._last_outcome_detail = detail
        self._last_outcome_cause = cause

    def _record_section_health(self, section_id: str) -> None:
        mapping = {
            "success": {
                "severity": "info",
                "message": "LLM 생성 성공",
                "possible_cause": "",
            },
            "missing_codex_cli_fallback": {
                "severity": "critical",
                "message": "Codex CLI 미탑재로 fallback 텍스트를 사용했습니다.",
                "possible_cause": "러너 환경에 codex 바이너리가 없거나 PATH 설정이 누락됐을 수 있습니다.",
            },
            "json_parse_fallback": {
                "severity": "warning",
                "message": "LLM 응답 JSON 파싱 실패로 fallback 텍스트를 사용했습니다.",
                "possible_cause": "모델 출력이 스키마를 벗어나거나 응답이 중간에 잘렸을 수 있습니다.",
            },
            "codex_exit": {
                "severity": "warning",
                "message": "Codex CLI 실행 오류로 fallback 텍스트를 사용했습니다.",
                "possible_cause": "인증(auth.json), 네트워크, 모델명/CLI 옵션 문제일 수 있습니다.",
            },
            "empty_codex_output": {
                "severity": "warning",
                "message": "Codex 응답이 비어 fallback 텍스트를 사용했습니다.",
                "possible_cause": "일시적 네트워크 이슈 또는 CLI 내부 오류일 수 있습니다.",
            },
            "timeout": {
                "severity": "warning",
                "message": "Codex 응답 타임아웃으로 fallback 텍스트를 사용했습니다.",
                "possible_cause": "일시적 지연 또는 모델 응답 지연일 수 있습니다.",
            },
            "exception": {
                "severity": "warning",
                "message": "LLM 처리 중 예외가 발생해 fallback 텍스트를 사용했습니다.",
                "possible_cause": "환경 변수/실행 환경 불일치 또는 예상치 못한 런타임 오류일 수 있습니다.",
            },
            "empty_response_fallback": {
                "severity": "warning",
                "message": "LLM 결과가 비어 fallback 텍스트를 사용했습니다.",
                "possible_cause": "응답 파싱 실패 또는 CLI 출력 누락 가능성이 있습니다.",
            },
        }
        default_payload = mapping["exception"]
        payload = mapping.get(self._last_outcome_code, default_payload).copy()
        if self._last_outcome_detail:
            payload["message"] = f"{payload['message']} ({self._last_outcome_detail})"
        if self._last_outcome_cause:
            payload["possible_cause"] = self._last_outcome_cause
        payload["code"] = self._last_outcome_code
        self._section_health[section_id] = payload

    def health_snapshot(self) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        for section_id, info in self._section_health.items():
            severity = info.get("severity", "info")
            if severity not in {"warning", "critical"}:
                continue
            issues.append(
                {
                    "source": "llm",
                    "section_id": section_id,
                    "severity": severity,
                    "code": info.get("code", ""),
                    "message": info.get("message", ""),
                    "possible_cause": info.get("possible_cause", ""),
                }
            )

        status = "ok"
        if any(issue.get("severity") == "critical" for issue in issues):
            status = "critical"
        elif issues:
            status = "warning"

        return {
            "status": status,
            "issues": issues,
            "sections": self._section_health,
        }

    def write(self, section: SectionConfig, context: dict[str, Any]) -> str:
        self._set_outcome("unknown")
        if not shutil.which(self._codex_bin):
            self._log(f"section={section.id} llm=missing_codex_cli_fallback")
            self._set_outcome("missing_codex_cli_fallback")
            if self._strict:
                self._record_section_health(section.id)
                raise RuntimeError(f"Codex CLI not found: {self._codex_bin}")
            rendered = self._write_fallback(section=section, context=context)
            self._record_section_health(section.id)
            return rendered

        rendered = self._write_with_codex_cli(section=section, context=context)
        if rendered:
            if self._last_outcome_code == "unknown":
                self._set_outcome("success")
            if self._last_outcome_code == "success":
                self._log(f"section={section.id} llm=success")
            else:
                self._log(f"section={section.id} llm={self._last_outcome_code}")
            self._record_section_health(section.id)
            return rendered
        self._log(f"section={section.id} llm=empty_response_fallback")
        self._set_outcome("empty_response_fallback")
        if self._strict:
            self._record_section_health(section.id)
            raise RuntimeError(f"LLM generation returned empty text for section: {section.id}")

        fallback = self._write_fallback(section=section, context=context)
        self._record_section_health(section.id)
        return fallback

    def _write_with_codex_cli(self, section: SectionConfig, context: dict[str, Any]) -> str:
        prompt = self._build_codex_prompt(section=section, context=context)
        command: list[str] = [
            self._codex_bin,
            "exec",
            "-c",
            'approval_policy="never"',
            "--sandbox",
            "read-only",
            "--ephemeral",
        ]
        if self._llm_config.model:
            command.extend(["--model", self._llm_config.model])
        try:
            with tempfile.TemporaryDirectory(prefix="daily-report-codex-") as tmp_dir:
                output_path = Path(tmp_dir) / "last_message.txt"
                command.extend(["--output-last-message", str(output_path)])
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self._codex_timeout_sec,
                    check=False,
                )
                if completed.returncode != 0:
                    self._log(
                        f"section={section.id} llm=error type=codex_exit detail={completed.returncode} "
                        f"stderr_tail={self._tail(completed.stderr)}"
                    )
                    self._set_outcome(
                        "codex_exit",
                        detail=f"exit={completed.returncode}",
                        cause=self._tail(completed.stderr),
                    )
                    return ""

                raw_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
                if not raw_text:
                    raw_text = str(completed.stdout or "").strip()
                if not raw_text:
                    self._log(f"section={section.id} llm=error type=empty_codex_output")
                    self._set_outcome("empty_codex_output")
                    return ""

                return self._render_json_response(section=section, context=context, raw_text=raw_text)
        except subprocess.TimeoutExpired:
            self._log(f"section={section.id} llm=error type=timeout timeout_sec={self._codex_timeout_sec}")
            self._set_outcome("timeout", detail=f"timeout={self._codex_timeout_sec}s")
            return ""
        except Exception as exc:
            self._log(f"section={section.id} llm=error type={type(exc).__name__} detail={exc}")
            self._set_outcome("exception", detail=type(exc).__name__)
            return ""

    def _build_codex_prompt(self, section: SectionConfig, context: dict[str, Any]) -> str:
        return (
            "다음 지시를 따라 최종 답변만 작성하세요.\n"
            "1) 쉘 명령/파일 수정/도구 사용 없이 텍스트 생성만 수행하세요.\n"
            "2) 최종 답변은 JSON 객체 1개만 출력하세요.\n"
            "3) 코드 블록(```), 설명 문장, 마크다운을 포함하지 마세요.\n\n"
            + self._build_json_prompt(section=section, context=context)
        )

    def _tail(self, text: str, limit: int = 280) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(compact) <= limit:
            return compact
        return compact[-limit:]

    def _build_json_prompt(self, section: SectionConfig, context: dict[str, Any]) -> str:
        base = (
            "당신은 한국 부동산 시황 분석가입니다. "
            "반드시 한국어로 작성하고, 오직 JSON 객체 1개만 출력하세요. "
            "코드 블록(```), 설명 문장, 주석, 마크다운을 절대 포함하지 마세요.\n\n"
            f"섹션 제목: {section.title}\n"
            f"작성 지시: {section.instruction}\n"
            f"언어 코드: {self._language}\n"
            f"컨텍스트 JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
            "공통 금지 표현: 알려주시면, 해드리겠습니다, 도와드리겠습니다, 제공해 주시면.\n"
        )

        if section.id == "policy_news":
            return (
                base
                + "반드시 아래 JSON 스키마를 따르세요.\n"
                + '{\n'
                + '  "core_summary": "문단 2~4문장",\n'
                + '  "end_user": {"impact": ["문장", "..."], "actions": ["문장", "..."]},\n'
                + '  "owner": {"impact": ["문장", "..."], "actions": ["문장", "..."]},\n'
                + '  "investor": {"impact": ["문장", "..."], "actions": ["문장", "..."]}\n'
                + '}\n'
                + "규칙: impact/actions 배열은 각 2~4개 문자열."
            )

        if section.id == "price_trend":
            return (
                base
                + "반드시 아래 JSON 스키마를 따르세요.\n"
                + '{\n'
                + '  "summary_points": ["해석 문장", "해석 문장", "해석 문장"]\n'
                + '}\n'
                + "규칙: 3~5개 문자열. 가격 표현은 억 원 단위이며 중앙값 기준으로 해석."
            )

        if section.id == "insights":
            return (
                base
                + "반드시 아래 JSON 스키마를 따르세요.\n"
                + '{\n'
                + '  "top_movers": ["문장", "..."],\n'
                + '  "volume_changes": ["문장", "..."],\n'
                + '  "cautions": ["문장", "..."]\n'
                + '}\n'
                + "규칙: 각 배열은 2~5개 문자열."
            )

        if section.id == "buy_readiness":
            return (
                base
                + "반드시 아래 JSON 스키마를 따르세요.\n"
                + '{\n'
                + '  "status": "준비 완료|준비 진행|준비 필요 중 하나",\n'
                + '  "summary": "2~3문장",\n'
                + '  "checkpoints": ["점검 문장", "..."],\n'
                + '  "next_actions": ["행동 문장", "..."]\n'
                + '}\n'
                + "규칙: checkpoints/next_actions는 각 2~4개 문자열."
            )

        if section.id == "today_signal":
            return (
                base
                + "반드시 아래 JSON 스키마를 따르세요.\n"
                + '{\n'
                + '  "verdict": "적극매수|매수|관망|매도|적극매도 중 하나",\n'
                + '  "confidence": "상|중|하 중 하나",\n'
                + '  "basis": ["근거 문장", "근거 문장", "근거 문장"]\n'
                + '}\n'
                + "규칙: basis는 2~3개 문자열. verdict/confidence는 허용값 외 출력 금지."
            )

        return (
            base
            + "반드시 아래 JSON 스키마를 따르세요.\n"
            + '{\n'
            + '  "summary_points": ["문장", "문장", "문장"]\n'
            + '}'
        )

    def _render_json_response(self, section: SectionConfig, context: dict[str, Any], raw_text: str) -> str:
        payload = self._parse_json_object(raw_text)
        if payload is None:
            self._log(f"section={section.id} llm=json_parse_fallback")
            self._set_outcome("json_parse_fallback")
            return self._write_fallback(section=section, context=context)
        self._log(f"section={section.id} llm=json_parse_ok")
        self._set_outcome("success")

        if section.id == "policy_news":
            return self._render_policy_news(payload)
        if section.id == "price_trend":
            return self._render_price_trend(payload)
        if section.id == "insights":
            return self._render_insights(payload)
        if section.id == "buy_readiness":
            return self._render_buy_readiness(payload, context)
        if section.id == "today_signal":
            return self._render_today_signal(payload)

        return self._render_generic(payload)

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None

        candidates: list[str] = [text.strip()]
        stripped = text.strip()

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(block.strip() for block in fenced_blocks if block.strip())

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1].strip())

        tried: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in tried:
                continue
            tried.add(candidate)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    def _render_policy_news(self, payload: dict[str, Any]) -> str:
        summary = self._clean_sentence(payload.get("core_summary", ""))

        end_user = self._section_actor(payload.get("end_user"))
        owner = self._section_actor(payload.get("owner"))
        investor = self._section_actor(payload.get("investor"))

        lines: list[str] = ["### 핵심 요약"]
        lines.append(summary or "핵심 정책 변화가 확인되었으나 요약 문장 생성이 제한되었습니다.")
        lines.append("")

        lines.extend(self._render_actor_block("실수요자 영향 및 행동 제언", end_user))
        lines.append("")
        lines.extend(self._render_actor_block("보유자 영향 및 행동 제언", owner))
        lines.append("")
        lines.extend(self._render_actor_block("투자자 영향 및 행동 제언", investor))

        return "\n".join(lines).strip()

    def _render_price_trend(self, payload: dict[str, Any]) -> str:
        points = self._string_list(payload.get("summary_points"), min_items=3, max_items=5)
        if not points:
            return "- 가격/거래량 변동을 해석할 핵심 포인트가 부족합니다."
        return "\n".join(f"- {point}" for point in points)

    def _render_insights(self, payload: dict[str, Any]) -> str:
        top_movers = self._string_list(payload.get("top_movers"), min_items=2, max_items=5)
        volume_changes = self._string_list(payload.get("volume_changes"), min_items=2, max_items=5)
        cautions = self._string_list(payload.get("cautions"), min_items=2, max_items=5)

        lines: list[str] = ["- 주요 변동 지역"]
        self._append_child_bullets(
            lines=lines,
            items=top_movers,
            fallback="상대적으로 두드러진 변동 지역이 확인되지 않았습니다.",
        )
        lines.append("")
        lines.append("- 거래량 변화")
        self._append_child_bullets(
            lines=lines,
            items=volume_changes,
            fallback="거래량 변화 신호가 제한적입니다.",
        )
        lines.append("")
        lines.append("- 해석 시 유의사항")
        self._append_child_bullets(
            lines=lines,
            items=cautions,
            fallback="표본 수와 정책 변수의 영향을 함께 고려해야 합니다.",
        )

        return "\n".join(lines).strip()

    def _render_today_signal(self, payload: dict[str, Any]) -> str:
        verdict = self._normalize_signal_label(payload.get("verdict", payload.get("signal", "")))
        confidence = self._normalize_confidence_label(payload.get("confidence", ""))
        basis = self._string_list(payload.get("basis"), min_items=1, max_items=4)

        if not verdict:
            verdict = "관망"
        if not confidence:
            confidence = "중"
        if not basis:
            basis = ["가격·거래량·정책 신호가 혼재되어 보수적 접근이 필요합니다."]

        verdict_view = self._format_signal_with_emoji(verdict)
        confidence_view = self._format_confidence_with_emoji(confidence)
        lines: list[str] = [
            f"- 오늘의 한마디: {verdict_view}",
            f"- 신뢰도: {confidence_view}",
            "- 기준: 관심 지역/아파트, 단기(1~2주) 관점",
            "- 근거",
        ]
        self._append_child_bullets(
            lines=lines,
            items=basis,
            fallback="핵심 지표 신호가 제한적이어서 관망이 적절합니다.",
        )
        return "\n".join(lines).strip()

    def _render_buy_readiness(self, payload: dict[str, Any], context: dict[str, Any]) -> str:
        status = self._normalize_readiness_status(payload.get("status", context.get("readiness_status", "")))
        summary = self._clean_sentence(payload.get("summary", ""))
        checkpoints = self._string_list(payload.get("checkpoints"), min_items=2, max_items=4)
        next_actions = self._string_list(payload.get("next_actions"), min_items=2, max_items=4)

        if not status:
            status = self._normalize_readiness_status(context.get("readiness_status", "")) or "준비 필요"
        if not summary:
            summary = (
                "현재 입력한 예산/대출 조건에서 매수 준비도를 점검했습니다. "
                "필요 현금과 월 상환 부담을 함께 보고 실행 순서를 정하는 것이 핵심입니다."
            )
        if not checkpoints:
            checkpoints = self._default_buy_readiness_checkpoints(context)
        if not next_actions:
            next_actions = self._default_buy_readiness_actions(context)

        lines: list[str] = [
            f"- 준비 상태: {status}",
            f"- 요약: {summary}",
            "- 핵심 수치",
            f"    - 목표 매수가(중앙): {self._fmt_eok(context.get('target_price_mid_eok'))} "
            f"(범위 {self._fmt_eok(context.get('target_price_min_eok'))}~{self._fmt_eok(context.get('target_price_max_eok'))})",
            f"    - 추정 필요현금: {self._fmt_manwon(context.get('required_cash_manwon'))}",
            f"    - 보유현금/현금갭: {self._fmt_manwon(context.get('available_cash_manwon'))} / "
            f"{self._fmt_manwon(context.get('cash_gap_manwon'))}",
            f"    - 예상 월상환액(기준/스트레스): {self._fmt_manwon(context.get('monthly_payment_base_manwon'))} / "
            f"{self._fmt_manwon(context.get('monthly_payment_stress_manwon'))}",
            f"    - 월상환 부담률(기준/스트레스): {self._fmt_pct(context.get('monthly_burden_base_pct'))} / "
            f"{self._fmt_pct(context.get('monthly_burden_stress_pct'))}",
        ]
        months_to_goal = context.get("months_to_goal")
        if months_to_goal is None:
            lines.append("    - 목표 자금까지 예상 기간: 저축액 입력이 없어 계산 불가")
        else:
            lines.append(f"    - 목표 자금까지 예상 기간: {int(months_to_goal)}개월")

        lines.extend(["- 점검 포인트"])
        self._append_child_bullets(
            lines=lines,
            items=checkpoints,
            fallback="필요 현금과 월 상환 부담을 함께 점검합니다.",
        )
        lines.extend(["- 다음 행동"])
        self._append_child_bullets(
            lines=lines,
            items=next_actions,
            fallback="예산 범위와 대출 가능 금액을 먼저 확정합니다.",
        )
        return "\n".join(lines).strip()

    def _render_generic(self, payload: dict[str, Any]) -> str:
        points = self._string_list(payload.get("summary_points"), min_items=1, max_items=6)
        if not points:
            return "- 핵심 요약을 생성하지 못했습니다."
        return "\n".join(f"- {point}" for point in points)

    def _normalize_signal_label(self, value: Any) -> str:
        raw = re.sub(r"\s+", "", str(value or "")).upper()
        aliases = {
            "적극매수": "적극매수",
            "매수": "매수",
            "관망": "관망",
            "매도": "매도",
            "적극매도": "적극매도",
            "STRONGBUY": "적극매수",
            "BUY": "매수",
            "HOLD": "관망",
            "WAIT": "관망",
            "SELL": "매도",
            "STRONGSELL": "적극매도",
        }
        return aliases.get(raw, "")

    def _normalize_confidence_label(self, value: Any) -> str:
        raw = re.sub(r"\s+", "", str(value or "")).upper()
        aliases = {
            "상": "상",
            "중": "중",
            "하": "하",
            "HIGH": "상",
            "MEDIUM": "중",
            "MID": "중",
            "LOW": "하",
        }
        return aliases.get(raw, "")

    def _normalize_readiness_status(self, value: Any) -> str:
        raw = re.sub(r"\s+", "", str(value or "")).upper()
        aliases = {
            "준비완료": "준비 완료",
            "준비진행": "준비 진행",
            "준비필요": "준비 필요",
            "READY": "준비 완료",
            "INPROGRESS": "준비 진행",
            "IN_PROGRESS": "준비 진행",
            "NOTREADY": "준비 필요",
        }
        return aliases.get(raw, "")

    def _fmt_manwon(self, value: Any) -> str:
        amount = self._safe_float(value, default=0.0)
        sign = "-" if amount < 0 else ""
        absolute = abs(amount)
        eok = absolute / 10000.0
        return f"{sign}{absolute:,.1f}만원 ({sign}{eok:.2f}억)"

    def _fmt_eok(self, value: Any) -> str:
        return f"{self._safe_float(value, default=0.0):.2f}억"

    def _fmt_pct(self, value: Any) -> str:
        return f"{self._safe_float(value, default=0.0):.1f}%"

    def _default_buy_readiness_checkpoints(self, context: dict[str, Any]) -> list[str]:
        gap = self._safe_float(context.get("cash_gap_manwon"))
        stress = self._safe_float(context.get("monthly_burden_stress_pct"))
        threshold = self._safe_float(context.get("affordability_threshold_pct"), default=35.0)
        points: list[str] = []
        if gap > 0:
            points.append(f"현재 추정 기준 필요현금 대비 {self._fmt_manwon(gap)} 부족합니다.")
        else:
            points.append("필요현금은 충족 상태이며 실행 시점과 금리 조건 점검이 우선입니다.")

        if stress > threshold:
            points.append(
                f"스트레스 금리 부담률 {stress:.1f}%가 목표 상한 {threshold:.1f}%를 상회해 상환 리스크가 큽니다."
            )
        else:
            points.append(
                f"스트레스 금리 부담률 {stress:.1f}%가 목표 상한 {threshold:.1f}% 이내로 관리 가능한 수준입니다."
            )

        market_gap = self._safe_float(context.get("target_vs_market_pct"))
        if market_gap >= 0:
            points.append(f"목표 매수가 중앙값이 현재 시장 기준 대비 {market_gap:.1f}% 높아 협상/하락 구간 포착이 필요합니다.")
        else:
            points.append(f"목표 매수가 중앙값이 현재 시장 기준 대비 {abs(market_gap):.1f}% 낮아 후보 단지 선별이 필요합니다.")
        return points

    def _default_buy_readiness_actions(self, context: dict[str, Any]) -> list[str]:
        months_to_goal = context.get("months_to_goal")
        actions = [
            "매수 후보 단지 3~5개를 고정하고 최근 90일 실거래 중앙값과 최저 체결가를 매주 업데이트합니다.",
            "대출 사전상담으로 실제 가능 LTV/DSR 범위를 확인하고, 같은 조건으로 월 상환액을 재산출합니다.",
        ]
        if months_to_goal is None:
            actions.append("월 저축 가능액을 확정해 목표 자금 달성 시점을 산출합니다.")
        else:
            actions.append(f"현재 저축 속도 기준 목표 자금 도달 예상 {int(months_to_goal)}개월을 기준으로 매수 시점을 계획합니다.")
        return actions

    def _format_signal_with_emoji(self, verdict: str) -> str:
        emoji = {
            "적극매수": "🚀",
            "매수": "📈",
            "관망": "👀",
            "매도": "📉",
            "적극매도": "🧯",
        }.get(verdict, "📝")
        return f"{emoji} {verdict}"

    def _format_confidence_with_emoji(self, confidence: str) -> str:
        emoji = {
            "상": "🟢",
            "중": "🟡",
            "하": "🔴",
        }.get(confidence, "⚪")
        return f"{emoji} {confidence}"

    def _section_actor(self, value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {"impact": [], "actions": []}
        return {
            "impact": self._string_list(value.get("impact"), min_items=0, max_items=4),
            "actions": self._string_list(value.get("actions"), min_items=0, max_items=4),
        }

    def _render_actor_block(self, title: str, block: dict[str, list[str]]) -> list[str]:
        lines = [f"### {title}", "- 영향"]
        self._append_child_bullets(
            lines=lines,
            items=block.get("impact", []),
            fallback="직접적 영향은 제한적이나 정책 변화 모니터링이 필요합니다.",
        )
        lines.append("- 행동 제언")
        self._append_child_bullets(
            lines=lines,
            items=block.get("actions", []),
            fallback="보수적 자금 계획과 정책 변경 점검을 병행합니다.",
        )
        return lines

    def _append_child_bullets(self, lines: list[str], items: list[str], fallback: str) -> None:
        if not items:
            lines.append(f"    - {fallback}")
            return
        for item in items:
            lines.append(f"    - {item}")

    def _string_list(self, value: Any, min_items: int, max_items: int) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = [self._clean_sentence(item) for item in value if isinstance(item, str)]
        cleaned = [item for item in cleaned if item]
        if len(cleaned) < min_items:
            return cleaned
        return cleaned[:max_items]

    def _clean_sentence(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value)).strip()
        if not text:
            return ""

        banned_phrases = (
            "알려주시면",
            "해드리겠습니다",
            "해 드리겠습니다",
            "도와드리겠습니다",
            "제공해 주시면",
            "제공해주시면",
            "즉시 계산해",
            "진행 방법",
        )
        if any(phrase in text for phrase in banned_phrases):
            return ""
        return text

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _compute_signal_score(self, context: dict[str, Any]) -> float:
        metric_count = int(context.get("metric_count", 0) or 0)
        if metric_count <= 0:
            return 0.0

        avg_daily = self._safe_float(context.get("avg_daily_change_pct"))
        avg_weekly = self._safe_float(context.get("avg_weekly_change_pct"))
        avg_txn = self._safe_float(context.get("avg_txn_daily_change_pct"))
        positive_daily = int(context.get("positive_daily_count", 0) or 0)
        negative_daily = int(context.get("negative_daily_count", 0) or 0)
        top_movers = context.get("top_movers", [])

        score = 0.0
        if avg_daily >= 1.0:
            score += 1.0
        elif avg_daily <= -1.0:
            score -= 1.0

        if avg_weekly >= 1.5:
            score += 1.0
        elif avg_weekly <= -1.5:
            score -= 1.0

        if avg_txn >= 15.0:
            score += 0.5
        elif avg_txn <= -15.0:
            score -= 0.5

        if positive_daily >= negative_daily + 2:
            score += 0.5
        elif negative_daily >= positive_daily + 2:
            score -= 0.5

        if isinstance(top_movers, list) and top_movers:
            first = top_movers[0]
            if isinstance(first, dict):
                lead_change = self._safe_float(first.get("daily_change_pct"))
                if abs(lead_change) >= 6.0:
                    score += 0.5 if lead_change > 0 else -0.5

        return max(-2.0, min(2.0, score))

    def _score_to_signal(self, score: float) -> str:
        if score >= 1.5:
            return "적극매수"
        if score >= 0.5:
            return "매수"
        if score > -0.5:
            return "관망"
        if score > -1.5:
            return "매도"
        return "적극매도"

    def _score_to_confidence(self, context: dict[str, Any], score: float) -> str:
        market_data_mode = str(context.get("market_data_mode", ""))
        metric_count = int(context.get("metric_count", 0) or 0)

        if market_data_mode in {"public_api_error", "public_api_empty", "synthetic"}:
            return "하"
        if metric_count <= 1:
            return "하"
        if metric_count >= 8 and abs(score) >= 1.0:
            return "상"
        if metric_count >= 3:
            return "중"
        return "하"

    def _write_today_signal_fallback(self, context: dict[str, Any]) -> str:
        score = self._compute_signal_score(context)
        verdict = self._score_to_signal(score)
        confidence = self._score_to_confidence(context, score)
        verdict_view = self._format_signal_with_emoji(verdict)
        confidence_view = self._format_confidence_with_emoji(confidence)

        avg_daily = self._safe_float(context.get("avg_daily_change_pct"))
        avg_weekly = self._safe_float(context.get("avg_weekly_change_pct"))
        avg_txn = self._safe_float(context.get("avg_txn_daily_change_pct"))
        metric_count = int(context.get("metric_count", 0) or 0)
        news_count = int(context.get("news_count", 0) or 0)
        top_movers = context.get("top_movers", [])

        basis = [
            "평균 일간 변화율 {daily:+.2f}%, 주간 변화율 {weekly:+.2f}%로 단기 추세를 반영했습니다.".format(
                daily=avg_daily,
                weekly=avg_weekly,
            ),
            "거래량 전일 대비 평균 변화율은 {txn:+.2f}%이며 표본 수는 {count}개입니다.".format(
                txn=avg_txn,
                count=metric_count,
            ),
            "정책 뉴스 {count}건을 참고해 이벤트 리스크를 함께 고려했습니다.".format(count=news_count),
        ]

        if isinstance(top_movers, list) and top_movers:
            lead = top_movers[0]
            if isinstance(lead, dict):
                region = str(lead.get("region_name", "")).strip()
                asset = str(lead.get("asset", "")).strip()
                asset_label = {
                    "apartment": "아파트",
                    "villa": "빌라",
                    "officetel": "오피스텔",
                }.get(asset, asset)
                change = self._safe_float(lead.get("daily_change_pct"))
                if region and asset_label:
                    basis[2] = "{region} {asset}의 전일 변동률 {change:+.2f}%가 대표 신호로 반영되었습니다.".format(
                        region=region,
                        asset=asset_label,
                        change=change,
                    )

        lines = [
            f"- 오늘의 한마디: {verdict_view}",
            f"- 신뢰도: {confidence_view}",
            "- 기준: 관심 지역/아파트, 단기(1~2주) 관점",
            "- 근거",
        ]
        self._append_child_bullets(lines=lines, items=basis, fallback="시장 신호가 제한적입니다.")
        return "\n".join(lines).strip()

    def _write_buy_readiness_fallback(self, context: dict[str, Any]) -> str:
        status = self._normalize_readiness_status(context.get("readiness_status", "")) or "준비 필요"
        summary = (
            "매수 준비도는 필요현금, 월 상환 부담, 목표 도달 기간을 함께 봐야 정확합니다. "
            "지금 수치는 입력한 조건 기준 시뮬레이션 결과입니다."
        )
        payload = {
            "status": status,
            "summary": summary,
            "checkpoints": self._default_buy_readiness_checkpoints(context),
            "next_actions": self._default_buy_readiness_actions(context),
        }
        return self._render_buy_readiness(payload, context)

    def _log(self, message: str) -> None:
        if self._debug:
            print(f"[LLM] {message}", file=sys.stderr)

    def _write_fallback(self, section: SectionConfig, context: dict[str, Any]) -> str:
        asset_map = {
            "apartment": "아파트",
            "villa": "빌라",
            "officetel": "오피스텔",
        }

        if section.id == "policy_news":
            items = context.get("news_items", [])
            if not items:
                return "\n".join(
                    [
                        "### 핵심 요약",
                        "이번 집계 구간에서 주요 부동산 정책 뉴스가 확인되지 않았습니다.",
                        "",
                        "### 실수요자 영향 및 행동 제언",
                        "- 영향",
                        "    - 정책 뉴스가 부족하여 직접적 영향 판단이 제한적입니다.",
                        "- 행동 제언",
                        "    - 다음 집계 구간에서 정책 발표 및 규제 변경 여부를 확인합니다.",
                        "",
                        "### 보유자 영향 및 행동 제언",
                        "- 영향",
                        "    - 보유 전략을 조정할 수준의 신규 정책 신호가 제한적입니다.",
                        "- 행동 제언",
                        "    - 대출 만기와 세금 일정을 점검하며 보수적으로 대응합니다.",
                        "",
                        "### 투자자 영향 및 행동 제언",
                        "- 영향",
                        "    - 단기 투자 판단에 필요한 정책 이벤트가 부족합니다.",
                        "- 행동 제언",
                        "    - 레버리지 비율을 낮게 유지하고 정책 발표 일정을 모니터링합니다.",
                    ]
                )
            return "\n".join(
                [
                    "### 핵심 요약",
                    f"정책 뉴스 {len(items)}건이 수집되었으며 주요 이슈 중심으로 해석이 필요합니다.",
                    "",
                    "### 실수요자 영향 및 행동 제언",
                    "- 영향",
                    "    - 정책 방향성에 따라 대출 접근성과 거래 심리가 변동할 수 있습니다.",
                    "- 행동 제언",
                    "    - 계약 전 대출 조건과 규제지역 여부를 우선 확인합니다.",
                    "",
                    "### 보유자 영향 및 행동 제언",
                    "- 영향",
                    "    - 규제 강도 변화에 따라 보유비용과 유동성 리스크가 달라질 수 있습니다.",
                    "- 행동 제언",
                    "    - 보유 자산별 만기/세금/현금흐름 시나리오를 점검합니다.",
                    "",
                    "### 투자자 영향 및 행동 제언",
                    "- 영향",
                    "    - 정책 기대 변화가 단기 수익률 변동성을 키울 수 있습니다.",
                    "- 행동 제언",
                    "    - 단기 고레버리지 전략보다 방어적 포지션을 우선합니다.",
                ]
            )

        if section.id == "price_trend":
            metrics = context.get("metrics", [])
            if not metrics:
                return "- 집계 가능한 시세 지표가 없습니다."
            lines = []
            for metric in metrics[:4]:
                lines.append(
                    "{region} {asset}: 중앙가 {price:.2f}억, 전일 {d:+.2f}%, 전주 {w:+.2f}%, 거래 {txn}건".format(
                        region=metric["region_name"],
                        asset=asset_map.get(metric["asset"], metric["asset"]),
                        price=float(metric.get("current_median_price_eok", metric.get("current_avg_price_eok", 0.0))),
                        d=float(metric["daily_change_pct"]),
                        w=float(metric["weekly_change_pct"]),
                        txn=int(metric["current_txn_count"]),
                    )
                )
            return "\n".join(f"- {line}" for line in lines)

        if section.id == "insights":
            movers = context.get("top_movers", [])
            lines = ["- 주요 변동 지역"]
            if movers:
                for mover in movers[:4]:
                    lines.append(
                        "    - {region} {asset}: 가격 {change:+.2f}% (전일), 거래량 {txn:+.2f}% (전일)".format(
                            region=mover["region_name"],
                            asset=asset_map.get(mover["asset"], mover["asset"]),
                            change=float(mover["daily_change_pct"]),
                            txn=float(mover["txn_daily_change_pct"]),
                        )
                    )
            else:
                lines.append("    - 주요 변동 지역이 확인되지 않았습니다.")

            lines.extend(
                [
                    "",
                    "- 거래량 변화",
                    "    - 거래량 변화는 표본 수와 함께 해석해야 합니다.",
                    "",
                    "- 해석 시 유의사항",
                    "    - 표본 수가 매우 적으면 중앙값도 단기 왜곡될 수 있습니다.",
                ]
            )
            return "\n".join(lines)

        if section.id == "buy_readiness":
            return self._write_buy_readiness_fallback(context)

        if section.id == "today_signal":
            return self._write_today_signal_fallback(context)

        return "- 기본 생성기로 작성된 섹션입니다."
