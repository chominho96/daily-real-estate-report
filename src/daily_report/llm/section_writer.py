from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

from daily_report.models import LLMConfig, SectionConfig


class SectionWriter:
    def __init__(self, llm_config: LLMConfig, language: str) -> None:
        self._llm_config = llm_config
        self._language = language
        self._debug = os.getenv("LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._strict = os.getenv("LLM_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}

    def write(self, section: SectionConfig, context: dict[str, Any]) -> str:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            rendered = self._write_with_openai(section=section, context=context, api_key=api_key)
            if rendered:
                self._log(f"section={section.id} llm=success")
                return rendered
            self._log(f"section={section.id} llm=empty_response_fallback")
            if self._strict:
                raise RuntimeError(f"LLM generation returned empty text for section: {section.id}")
        else:
            self._log(f"section={section.id} llm=missing_api_key_fallback")

        return self._write_fallback(section=section, context=context)

    def _write_with_openai(self, section: SectionConfig, context: dict[str, Any], api_key: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            return ""

        prompt = (
            "당신은 한국 부동산 시황 분석가입니다. "
            "응답은 반드시 한국어로만 작성하고, 유효한 마크다운만 반환하세요. "
            "섹션 제목은 반복하지 말고 본문만 작성하세요.\n\n"
            f"섹션 제목: {section.title}\n"
            f"작성 지시: {section.instruction}\n"
            f"언어 코드: {self._language}\n"
            f"컨텍스트 JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}"
        )
        if section.id == "price_trend":
            prompt += "\n\n추가 규칙: 가격은 반드시 억 원 단위로만 표기하고, 가능하면 current_avg_price_eok 값을 사용하세요."

        try:
            client = OpenAI(api_key=api_key)
            response = self._create_response(client=client, prompt=prompt, use_temperature=True)
            text = str(getattr(response, "output_text", "") or "").strip()
            if not text:
                text = self._extract_text_from_response(response)
            return text
        except Exception as exc:
            if self._is_unsupported_temperature_error(exc):
                self._log(f"section={section.id} llm=retry_without_temperature")
                try:
                    response = self._create_response(client=client, prompt=prompt, use_temperature=False)
                    text = str(getattr(response, "output_text", "") or "").strip()
                    if not text:
                        text = self._extract_text_from_response(response)
                    return text
                except Exception as retry_exc:
                    self._log(f"section={section.id} llm=error type={type(retry_exc).__name__} detail={retry_exc}")
                    if self._debug:
                        self._log(traceback.format_exc().rstrip())
                    return ""

            self._log(f"section={section.id} llm=error type={type(exc).__name__} detail={exc}")
            if self._debug:
                self._log(traceback.format_exc().rstrip())
            return ""

    def _create_response(self, client: Any, prompt: str, use_temperature: bool) -> Any:
        params: dict[str, Any] = {
            "model": self._llm_config.model,
            "input": prompt,
        }
        if use_temperature:
            params["temperature"] = self._llm_config.temperature
        return client.responses.create(**params)

    def _is_unsupported_temperature_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "Unsupported parameter: 'temperature'" in message

    def _extract_text_from_response(self, response: Any) -> str:
        output_items = getattr(response, "output", None)
        if not output_items:
            return ""

        chunks: list[str] = []
        for item in output_items:
            content_items = getattr(item, "content", None)
            if content_items is None and isinstance(item, dict):
                content_items = item.get("content")
            if not content_items:
                continue

            for content in content_items:
                text = getattr(content, "text", None)
                if text is None and isinstance(content, dict):
                    text = content.get("text")
                if text:
                    chunks.append(str(text))

        return "\n".join(chunks).strip()

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
                return "- 이번 집계 구간에서 주요 부동산 정책 뉴스가 확인되지 않았습니다."
            lines = ["- 정책 핵심 뉴스:"]
            for item in items[:8]:
                lines.append(
                    f"  - [{item['title']}]({item['link']}) ({item['source']}, {item['published_at']})"
                )
            return "\n".join(lines)

        if section.id == "price_trend":
            metrics = context.get("metrics", [])
            if not metrics:
                return "- 집계 가능한 시세 지표가 없습니다."
            lines = ["| 지역 | 자산군 | 평균 가격(억 원) | 전일 대비 | 전주 대비 | 거래 건수 | 거래 건수(전일 대비) |", "|---|---:|---:|---:|---:|---:|---:|"]
            for metric in metrics:
                price_eok = float(metric["current_avg_price"]) / 10000.0
                lines.append(
                    "| {region} | {asset} | {price:.2f} | {d:+.2f}% | {w:+.2f}% | {txn} | {td:+.2f}% |".format(
                        region=metric["region_name"],
                        asset=asset_map.get(metric["asset"], metric["asset"]),
                        price=price_eok,
                        d=metric["daily_change_pct"],
                        w=metric["weekly_change_pct"],
                        txn=metric["current_txn_count"],
                        td=metric["txn_daily_change_pct"],
                    )
                )
            return "\n".join(lines)

        if section.id == "insights":
            movers = context.get("top_movers", [])
            if not movers:
                return "- 추가 인사이트를 만들기 위한 유의미한 변동 신호가 부족합니다."
            lines = ["- 주요 변동 지역:"]
            for mover in movers:
                lines.append(
                    "  - {region} {asset}: 가격 {change:+.2f}% (전일), {week:+.2f}% (전주), 거래량 {txn_change:+.2f}% (전일)".format(
                        region=mover["region_name"],
                        asset=asset_map.get(mover["asset"], mover["asset"]),
                        change=mover["daily_change_pct"],
                        week=mover["weekly_change_pct"],
                        txn_change=mover["txn_daily_change_pct"],
                    )
                )
            data_mode = str(context.get("market_data_mode", ""))
            if data_mode == "public_api_empty":
                lines.append("- 참고: 이번 집계 구간에는 공공 API에 매칭된 실거래 데이터가 없어 지표가 제한될 수 있습니다.")
            elif data_mode == "synthetic":
                lines.append("- 참고: 공공 API 설정이 없거나 응답 오류가 있어 샘플 데이터로 대체되었습니다.")
            return "\n".join(lines)

        return "- 기본 생성기로 작성된 섹션입니다."
