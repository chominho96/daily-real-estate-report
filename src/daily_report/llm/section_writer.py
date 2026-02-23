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

    def write(self, section: SectionConfig, context: dict[str, Any]) -> str:
        if not shutil.which(self._codex_bin):
            self._log(f"section={section.id} llm=missing_codex_cli_fallback")
            if self._strict:
                raise RuntimeError(f"Codex CLI not found: {self._codex_bin}")
            return self._write_fallback(section=section, context=context)

        rendered = self._write_with_codex_cli(section=section, context=context)
        if rendered:
            self._log(f"section={section.id} llm=success")
            return rendered
        self._log(f"section={section.id} llm=empty_response_fallback")
        if self._strict:
            raise RuntimeError(f"LLM generation returned empty text for section: {section.id}")

        return self._write_fallback(section=section, context=context)

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
                    return ""

                raw_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
                if not raw_text:
                    raw_text = str(completed.stdout or "").strip()
                if not raw_text:
                    self._log(f"section={section.id} llm=error type=empty_codex_output")
                    return ""

                return self._render_json_response(section=section, context=context, raw_text=raw_text)
        except subprocess.TimeoutExpired:
            self._log(f"section={section.id} llm=error type=timeout timeout_sec={self._codex_timeout_sec}")
            return ""
        except Exception as exc:
            self._log(f"section={section.id} llm=error type={type(exc).__name__} detail={exc}")
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
                + "규칙: 3~5개 문자열. 가격 표현은 억 원 단위."
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
            return self._write_fallback(section=section, context=context)
        self._log(f"section={section.id} llm=json_parse_ok")

        if section.id == "policy_news":
            return self._render_policy_news(payload)
        if section.id == "price_trend":
            return self._render_price_trend(payload)
        if section.id == "insights":
            return self._render_insights(payload)

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

    def _render_generic(self, payload: dict[str, Any]) -> str:
        points = self._string_list(payload.get("summary_points"), min_items=1, max_items=6)
        if not points:
            return "- 핵심 요약을 생성하지 못했습니다."
        return "\n".join(f"- {point}" for point in points)

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
                    "{region} {asset}: 평균가 {price:.2f}억, 전일 {d:+.2f}%, 전주 {w:+.2f}%, 거래 {txn}건".format(
                        region=metric["region_name"],
                        asset=asset_map.get(metric["asset"], metric["asset"]),
                        price=float(metric.get("current_avg_price_eok", 0.0)),
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
                    "    - 단일 거래가 평균 가격에 큰 영향을 줄 수 있습니다.",
                ]
            )
            return "\n".join(lines)

        return "- 기본 생성기로 작성된 섹션입니다."
