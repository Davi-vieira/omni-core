"""LLM suggestion layer for Omni-Core ERP."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Callable
from urllib import error, parse, request

from src.core import build_pricing_suggestion
from src.core.audit_logger import BusinessAuditLogger
from src.core.fsm import SaleFSM, SaleState
from src.database import ChatHistoryRepository, ProductRepository, get_connection
from src.domain.models import Product

MONEY_STEP = Decimal("0.01")
LOW_STOCK_THRESHOLD = 3
LOW_STOCK_WARNING = "\u26a0\ufe0f Estoque critico! Evitar descontos agressivos"
SAFE_MANUAL_MESSAGE = "Sistema em modo manual - consulte o motor de regras"


class BrainIntent(str, Enum):
    """Structured intents returned by the suggestion layer."""

    SAUDACAO = "saudacao"
    PESQUISA = "pesquisa"
    NEGOCIACAO = "negociacao"
    CHECKOUT = "checkout"
    AGRADECIMENTO = "agradecimento"
    MANUAL = "manual"


class BrainError(Exception):
    """Base exception for OmniBrain."""


class BrainAPIError(BrainError):
    """Raised when the external LLM provider fails."""


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """Simple in-memory conversation entry."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ProductContext:
    """Real-time product context sent to the LLM."""

    product_id: int
    sku: str
    nome: str
    categoria: str
    descricao: str
    estoque_atual: int
    preco_tabela: Decimal
    preco_minimo: Decimal
    preco_sugerido: Decimal
    quantidade_contexto: int


@dataclass(frozen=True, slots=True)
class BrainResponse:
    """Structured output returned by the suggestion layer."""

    session_id: str
    session_state: SaleState
    intent: BrainIntent
    product_id: int | None
    proposed_price: Decimal | None
    reply_text: str
    safe_mode: bool
    raw_response: str | None = None
    validation_note: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable payload for UI/API consumers."""

        return {
            "session_id": self.session_id,
            "session_state": self.session_state.value,
            "intent": self.intent.value,
            "product_id": self.product_id,
            "proposed_price": str(self.proposed_price) if self.proposed_price is not None else None,
            "reply_text": self.reply_text,
            "safe_mode": self.safe_mode,
            "validation_note": self.validation_note,
        }


class OmniBrain:
    """Suggestion-only sales assistant with real-time RAG over local data.

    The class never persists a sale. It only returns a structured suggestion
    that can later be routed to the FSM and deterministic rule engine.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        request_timeout: float = 10.0,
        history_limit: int = 8,
        transport: Callable[[str, dict[str, Any], float], str] | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        self._model = model or os.getenv("OMNICORE_LLM_MODEL", "gemini-1.5-flash")
        self._request_timeout = request_timeout
        self._transport = transport or self._default_transport
        self._history_limit = max(2, history_limit)
        self._memory: dict[str, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._hydrated_sessions: set[str] = set()

    def process_message(self, user_input: str, session_id: str) -> BrainResponse:
        """Process a user message and return a structured suggestion.

        The method consults the current FSM session, gathers real-time product
        context from SQLite, builds a strict prompt and validates the model
        output before returning it.
        """

        normalized_input = user_input.strip()
        if not session_id.strip():
            raise BrainError("session_id e obrigatorio para processar mensagens.")
        if not normalized_input:
            raise BrainError("user_input nao pode ser vazio.")

        self._hydrate_memory_from_persistence(session_id)
        session_snapshot = SaleFSM.get_snapshot(session_id) or self._default_session_snapshot(session_id)
        if session_snapshot.state == SaleState.CONCLUIDA:
            response = BrainResponse(
                session_id=session_id,
                session_state=session_snapshot.state,
                intent=BrainIntent.AGRADECIMENTO,
                product_id=session_snapshot.selected_product_id,
                proposed_price=None,
                reply_text="Pedido concluido com sucesso. Obrigado pela compra.",
                safe_mode=False,
                validation_note="Sessao encerrada; nenhuma nova negociacao foi iniciada.",
            )
            self._append_memory(session_id, "user", normalized_input)
            self._append_memory(
                session_id,
                "assistant",
                response.reply_text,
                metadata=self._assistant_metadata(response),
            )
            return response

        if session_snapshot.state == SaleState.CANCELADA:
            response = BrainResponse(
                session_id=session_id,
                session_state=session_snapshot.state,
                intent=BrainIntent.MANUAL,
                product_id=session_snapshot.selected_product_id,
                proposed_price=None,
                reply_text="Atendimento cancelado. Abra uma nova sessao para continuar negociando.",
                safe_mode=True,
                validation_note="Sessao atual esta cancelada e nao pode ser reutilizada.",
            )
            self._append_memory(session_id, "user", normalized_input)
            self._append_memory(
                session_id,
                "assistant",
                response.reply_text,
                metadata=self._assistant_metadata(response),
            )
            return response

        quantity = self._extract_quantity(normalized_input, session_snapshot)
        product_contexts = self._load_product_contexts(
            user_input=normalized_input,
            session_product_id=session_snapshot.selected_product_id,
            quantity=quantity,
        )

        prompt = self._build_prompt(
            user_input=normalized_input,
            session_state=session_snapshot.state,
            session_id=session_id,
            recent_history=list(self._memory[session_id]),
            product_contexts=product_contexts,
        )

        self._append_memory(session_id, "user", normalized_input)

        try:
            if not self._api_key:
                raise BrainAPIError("Chave da API Gemini nao configurada.")

            raw_response = self._transport(
                self._model,
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "topP": 0.8,
                        "responseMimeType": "application/json",
                    },
                },
                self._request_timeout,
            )
            response = self._validate_model_response(
                raw_response=raw_response,
                session_id=session_id,
                session_state=session_snapshot.state,
                product_contexts=product_contexts,
            )
        except BrainAPIError:
            response = BrainResponse(
                session_id=session_id,
                session_state=session_snapshot.state,
                intent=BrainIntent.MANUAL,
                product_id=product_contexts[0].product_id if product_contexts else session_snapshot.selected_product_id,
                proposed_price=None,
                reply_text=SAFE_MANUAL_MESSAGE,
                safe_mode=True,
                validation_note="Falha ou indisponibilidade da API de IA. Operacao mantida em modo manual.",
            )

        response = self._apply_stock_warning(response, product_contexts)
        self._record_business_audit(response, product_contexts)
        self._append_memory(
            session_id,
            "assistant",
            response.reply_text,
            metadata=self._assistant_metadata(response),
        )
        return response

    def discard_session(self, session_id: str) -> None:
        """Discard in-memory conversation history for a finished session."""

        if session_id.strip():
            self._memory.pop(session_id.strip(), None)
            self._hydrated_sessions.discard(session_id.strip())

    def _default_transport(self, model: str, payload: dict[str, Any], timeout: float) -> str:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{parse.quote(model)}:generateContent?key={parse.quote(self._api_key)}"
        )
        request_body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            endpoint,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=timeout) as http_response:
                body = http_response.read().decode("utf-8")
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            raise BrainAPIError("Falha ao consultar a API Gemini.") from exc

        try:
            payload_json = json.loads(body)
            return str(
                payload_json["candidates"][0]["content"]["parts"][0]["text"]
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise BrainAPIError("Resposta da API Gemini em formato inesperado.") from exc

    def _build_prompt(
        self,
        *,
        user_input: str,
        session_state: SaleState,
        session_id: str,
        recent_history: list[ConversationTurn],
        product_contexts: list[ProductContext],
    ) -> str:
        system_prompt = (
            "Voce e um assistente de vendas do Omni-Core. "
            "Voce pode sugerir precos entre o Preco de Tabela e o Preco Minimo. "
            "Voce NUNCA pode fechar uma venda; voce deve apenas sugerir a SaleProposal "
            "para o usuario humano ou para o Motor de Regras. "
            "Responda SOMENTE em JSON valido com as chaves: "
            "intent, product_id, proposed_price, reply_text. "
            "Intent permitido: saudacao, pesquisa, negociacao, checkout, agradecimento. "
            "Se nao houver produto confiavel, use product_id = null e proposed_price = null."
        )

        history_text = "\n".join(
            f"{turn.role}: {turn.content}" for turn in recent_history[-6:]
        ) or "sem historico recente"

        product_lines = []
        for item in product_contexts:
            product_lines.append(
                (
                    f"- id={item.product_id} | sku={item.sku} | nome={item.nome} | "
                    f"categoria={item.categoria} | estoque={item.estoque_atual} | "
                    f"preco_tabela={item.preco_tabela} | preco_minimo={item.preco_minimo} | "
                    f"preco_sugerido={item.preco_sugerido} | quantidade_contexto={item.quantidade_contexto}"
                )
            )
        products_block = "\n".join(product_lines) if product_lines else "- nenhum produto relevante encontrado"

        return (
            f"{system_prompt}\n\n"
            f"Estado atual da sessao: {session_state.value}\n"
            f"Session ID: {session_id}\n\n"
            f"Historico recente:\n{history_text}\n\n"
            f"Contexto em tempo real do banco:\n{products_block}\n\n"
            f"Mensagem do cliente:\n{user_input}\n"
        )

    def _load_product_contexts(
        self,
        *,
        user_input: str,
        session_product_id: int | None,
        quantity: int,
    ) -> list[ProductContext]:
        connection = get_connection()
        try:
            repository = ProductRepository(connection)
            products = repository.list_products(only_active=True)
        finally:
            connection.close()

        selected: list[Product] = []
        if session_product_id is not None:
            for product in products:
                if product.id == session_product_id:
                    selected.append(product)
                    break

        tokens = self._tokenize(user_input)
        scored: list[tuple[int, Product]] = []
        for product in products:
            if selected and product.id == selected[0].id:
                continue
            haystack = " ".join(
                (
                    product.sku.lower(),
                    product.nome.lower(),
                    product.categoria.lower(),
                    product.descricao.lower(),
                )
            )
            score = 0
            for token in tokens:
                if token in product.sku.lower():
                    score += 4
                if token in product.nome.lower():
                    score += 3
                if token in product.categoria.lower():
                    score += 2
                if token in product.descricao.lower():
                    score += 1
                if token in haystack:
                    score += 1
            if score > 0:
                scored.append((score, product))

        scored.sort(key=lambda item: (-item[0], item[1].nome))
        candidates = selected + [product for _, product in scored[:4]]

        contexts: list[ProductContext] = []
        seen_ids: set[int] = set()
        for product in candidates:
            if product.id is None or product.id in seen_ids:
                continue
            seen_ids.add(product.id)
            suggestion = build_pricing_suggestion(product.id, quantidade=quantity)
            contexts.append(
                ProductContext(
                    product_id=product.id,
                    sku=product.sku,
                    nome=product.nome,
                    categoria=product.categoria,
                    descricao=product.descricao,
                    estoque_atual=product.estoque_atual,
                    preco_tabela=product.preco_tabela,
                    preco_minimo=suggestion.preco_minimo_unitario,
                    preco_sugerido=suggestion.preco_sugerido_unitario,
                    quantidade_contexto=quantity,
                )
            )
        return contexts

    def _validate_model_response(
        self,
        *,
        raw_response: str,
        session_id: str,
        session_state: SaleState,
        product_contexts: list[ProductContext],
    ) -> BrainResponse:
        payload = self._load_json_payload(raw_response)

        intent = self._parse_intent(payload.get("intent"))
        product_id = self._parse_optional_int(payload.get("product_id"))
        proposed_price = self._parse_optional_decimal(payload.get("proposed_price"))
        reply_text = str(payload.get("reply_text") or "").strip()
        validation_note = ""

        context_by_product = {item.product_id: item for item in product_contexts}
        if product_id is not None and product_id not in context_by_product:
            validation_note = "Produto sugerido pela IA nao pertence ao contexto confiavel da sessao."
            product_id = None
            proposed_price = None
        if product_id is None:
            proposed_price = None

        if product_id is not None:
            context_product = context_by_product[product_id]
            if proposed_price is not None:
                if proposed_price < context_product.preco_minimo:
                    validation_note = (
                        "Preco sugerido pela IA estava abaixo do piso e foi ajustado para o minimo permitido."
                    )
                    proposed_price = context_product.preco_minimo
                elif proposed_price > context_product.preco_tabela:
                    validation_note = (
                        "Preco sugerido pela IA estava acima da tabela e foi ajustado para o preco de tabela."
                    )
                    proposed_price = context_product.preco_tabela
            elif intent == BrainIntent.NEGOCIACAO:
                proposed_price = context_product.preco_sugerido
                validation_note = "Preco ausente na resposta da IA; sugestao segura calculada pelo sistema."

        if not reply_text:
            reply_text = SAFE_MANUAL_MESSAGE
            validation_note = validation_note or "Resposta textual ausente; retornado fallback seguro."

        return BrainResponse(
            session_id=session_id,
            session_state=session_state,
            intent=intent,
            product_id=product_id,
            proposed_price=proposed_price,
            reply_text=reply_text,
            safe_mode=False,
            raw_response=raw_response,
            validation_note=validation_note,
        )

    def _apply_stock_warning(
        self,
        response: BrainResponse,
        product_contexts: list[ProductContext],
    ) -> BrainResponse:
        if response.product_id is None:
            return response

        context_by_product = {item.product_id: item for item in product_contexts}
        context_product = context_by_product.get(response.product_id)
        if context_product is None or context_product.estoque_atual >= LOW_STOCK_THRESHOLD:
            return response
        if LOW_STOCK_WARNING in response.reply_text:
            return response

        reply_text = f"{response.reply_text}\n\n{LOW_STOCK_WARNING}".strip()
        validation_note = response.validation_note
        if validation_note:
            validation_note = f"{validation_note} | Estoque critico sinalizado para a sessao."
        else:
            validation_note = "Estoque critico sinalizado para a sessao."

        return replace(
            response,
            reply_text=reply_text,
            validation_note=validation_note,
        )

    def _load_json_payload(self, raw_response: str) -> dict[str, Any]:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise BrainAPIError("A IA retornou um payload nao parseavel.")
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise BrainAPIError("A IA retornou JSON invalido.") from exc

        if not isinstance(payload, dict):
            raise BrainAPIError("O payload estruturado da IA nao e um objeto JSON.")
        return payload

    def _parse_intent(self, value: Any) -> BrainIntent:
        normalized = str(value or "").strip().lower()
        mapping = {
            "saudacao": BrainIntent.SAUDACAO,
            "saudação": BrainIntent.SAUDACAO,
            "pesquisa": BrainIntent.PESQUISA,
            "negociacao": BrainIntent.NEGOCIACAO,
            "negociação": BrainIntent.NEGOCIACAO,
            "checkout": BrainIntent.CHECKOUT,
            "fechamento": BrainIntent.CHECKOUT,
            "agradecimento": BrainIntent.AGRADECIMENTO,
        }
        return mapping.get(normalized, BrainIntent.MANUAL)

    @staticmethod
    def _parse_optional_int(value: Any) -> int | None:
        if value in (None, "", "null"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_optional_decimal(value: Any) -> Decimal | None:
        if value in (None, "", "null"):
            return None
        try:
            return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _extract_quantity(user_input: str, session_snapshot: Any) -> int:
        numbers = re.findall(r"\b(\d+)\b", user_input)
        if numbers:
            return max(1, int(numbers[0]))
        last_proposal = getattr(session_snapshot, "last_proposal", None)
        if last_proposal is not None:
            return max(1, int(last_proposal.quantidade))
        return 1

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[a-zA-Z0-9]{2,}", text.lower())
            if token not in {"de", "da", "do", "para", "com", "tem", "uma", "por"}
        ]

    @staticmethod
    def _default_session_snapshot(session_id: str):
        from src.core.fsm import SaleSessionSnapshot

        return SaleSessionSnapshot(
            session_id=session_id,
            state=SaleState.PESQUISA,
        )

    def _append_memory(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._memory[session_id].append(ConversationTurn(role=role, content=content))
        connection = get_connection()
        try:
            ChatHistoryRepository(connection).append_message(
                session_id=session_id,
                role=role,
                content=content,
                metadata=metadata,
            )
        finally:
            connection.close()

    def _hydrate_memory_from_persistence(self, session_id: str) -> None:
        normalized_session_id = session_id.strip()
        if not normalized_session_id or normalized_session_id in self._hydrated_sessions:
            return

        connection = get_connection()
        try:
            messages = ChatHistoryRepository(connection).list_recent_messages(
                session_id=normalized_session_id,
                limit=self._history_limit,
            )
        finally:
            connection.close()

        buffer = self._memory[normalized_session_id]
        buffer.clear()
        for message in messages:
            buffer.append(
                ConversationTurn(
                    role=message.role,
                    content=message.content,
                )
            )
        self._hydrated_sessions.add(normalized_session_id)

    @staticmethod
    def _assistant_metadata(response: BrainResponse) -> dict[str, Any]:
        return {
            "intent": response.intent.value,
            "product_id": response.product_id,
            "proposed_price": str(response.proposed_price) if response.proposed_price is not None else None,
            "safe_mode": response.safe_mode,
            "validation_note": response.validation_note,
            "session_state": response.session_state.value,
        }

    def _record_business_audit(
        self,
        response: BrainResponse,
        product_contexts: list[ProductContext],
    ) -> None:
        if response.product_id is None or response.proposed_price is None:
            return

        context_by_product = {item.product_id: item for item in product_contexts}
        context_product = context_by_product.get(response.product_id)
        if context_product is None:
            return
        if response.proposed_price >= context_product.preco_tabela:
            return

        reasoning = response.validation_note or response.reply_text
        try:
            BusinessAuditLogger.log_discount_suggestion(
                session_id=response.session_id,
                product_name=context_product.nome,
                table_price=context_product.preco_tabela,
                minimum_price=context_product.preco_minimo,
                proposed_price=response.proposed_price,
                reasoning=reasoning,
            )
        except Exception:
            return
