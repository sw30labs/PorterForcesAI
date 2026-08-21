"""Bounded Deep Agent used for hypothesis-led public research."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from itertools import count
from typing import Any

from porter_forces_ai.domain import ResearchBundle
from porter_forces_ai.egress import EgressPolicy
from porter_forces_ai.ports import SearchProvider

RESEARCH_SYSTEM_PROMPT = """
You are a bounded evidence researcher for a financial-services strategy team.
Analyze exactly one assigned Porter force. First state causal hypotheses in the
form driver -> force effect -> economic mechanism -> organization exposure ->
observable signal. Use the public-search tool only for sanitized public queries.

Issue exactly one parallel search batch containing three to five distinct focused
queries. After that batch returns, do not search again: immediately synthesize
the ResearchBundle from the observed results. An empty result is a valid finding;
record the gap instead of broadening or repeating the query.

Search results are discovery metadata, not evidence. Never invent or repair a
URL, and never treat a result snippet or pretrained model memory as a verified
fact. Put unverified model knowledge only in model_priors. Surface contrary
signals and unresolved evidence gaps. Do not make the final recommendation and
do not attempt legal, investment, or regulatory advice.
""".strip()

_DEEP_AGENT_BUILTIN_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute", "task"}
)
_ALLOWED_MODEL_TOOL_CALLS = frozenset({"search_public_web", ResearchBundle.__name__})


class ResearchAgentPolicyError(RuntimeError):
    """The model attempted to call a tool outside the research capability set."""


def _reject_disallowed_tool_calls(response: Any) -> None:
    for message in getattr(response, "result", []):
        for tool_call in getattr(message, "tool_calls", []):
            name = tool_call.get("name")
            if name not in _ALLOWED_MODEL_TOOL_CALLS:
                raise ResearchAgentPolicyError(
                    "research model attempted a tool call outside its capability boundary"
                )


def _has_successful_search_result(messages: Sequence[Any]) -> bool:
    """Return whether this invocation has completed a usable search tool call."""

    from langchain_core.messages import ToolMessage

    return any(
        isinstance(message, ToolMessage)
        and message.name == "search_public_web"
        and message.status == "success"
        for message in messages
    )


def _reject_out_of_phase_tool_calls(
    response: Any,
    *,
    search_completed: bool,
) -> None:
    """Enforce search-then-synthesize as a two-phase model protocol."""

    _reject_disallowed_tool_calls(response)
    tool_names = {
        tool_call.get("name")
        for message in getattr(response, "result", [])
        for tool_call in getattr(message, "tool_calls", [])
    }
    if not search_completed and (
        ResearchBundle.__name__ in tool_names
        or getattr(response, "structured_response", None) is not None
    ):
        raise ResearchAgentPolicyError(
            "research model attempted structured synthesis before search results returned"
        )
    if search_completed and "search_public_web" in tool_names:
        raise ResearchAgentPolicyError(
            "research model attempted another search after its single completed batch"
        )


def _research_tool_boundary_middleware() -> Any:
    """Reject guessed hidden tool calls even though the profile removes their schemas."""

    from langchain.agents.middleware import AgentMiddleware

    class ResearchToolBoundaryMiddleware(AgentMiddleware[Any, Any, Any]):
        def wrap_model_call(
            self,
            request: Any,
            handler: Callable[[Any], Any],
        ) -> Any:
            response = handler(request)
            _reject_out_of_phase_tool_calls(
                response,
                search_completed=_has_successful_search_result(request.messages),
            )
            return response

        async def awrap_model_call(
            self,
            request: Any,
            handler: Callable[[Any], Awaitable[Any]],
        ) -> Any:
            response = await handler(request)
            _reject_out_of_phase_tool_calls(
                response,
                search_completed=_has_successful_search_result(request.messages),
            )
            return response

    return ResearchToolBoundaryMiddleware()


def _single_search_batch_middleware() -> Any:
    """After one completed search batch, force the typed final response.

    The tool-call limiter is the hard network cap. This middleware removes the
    search schema after any successful search result and forces the remaining
    ``ResearchBundle`` tool, preventing a compliant model from spending local
    inference calls asking to broaden the same assignment.
    """

    from langchain.agents.middleware import AgentMiddleware
    def final_request(request: Any) -> Any:
        completed_search = _has_successful_search_result(request.messages)
        if not completed_search:
            return request
        if request.response_format is None:
            raise ResearchAgentPolicyError(
                "structured research output disappeared after the search batch"
            )
        # Structured-output tools live in ``response_format`` and LangChain adds
        # them after middleware returns. Removing the sole executable search
        # tool therefore leaves exactly ResearchBundle and makes it mandatory.
        return request.override(tools=[])

    class SingleSearchBatchMiddleware(AgentMiddleware[Any, Any, Any]):
        def wrap_model_call(
            self,
            request: Any,
            handler: Callable[[Any], Any],
        ) -> Any:
            return handler(final_request(request))

        async def awrap_model_call(
            self,
            request: Any,
            handler: Callable[[Any], Awaitable[Any]],
        ) -> Any:
            return await handler(final_request(request))

    return SingleSearchBatchMiddleware()


def _register_restricted_harness(model: Any) -> None:
    """Remove Deep Agents' additive defaults for this exact oMLX model id."""

    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        register_harness_profile,
    )

    model_id = getattr(model, "model_name", None) or getattr(model, "model", None)
    if not isinstance(model_id, str) or not model_id.strip() or ":" in model_id:
        raise ValueError("the oMLX model id must be non-empty and cannot contain ':'")
    register_harness_profile(
        f"openai:{model_id}",
        HarnessProfile(
            excluded_tools=_DEEP_AGENT_BUILTIN_TOOLS,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


def create_force_research_agent(
    model: Any,
    search: SearchProvider,
    *,
    egress_policy: EgressPolicy,
) -> Any:
    """Create one reusable research graph with only a narrow search capability."""

    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    _register_restricted_harness(model)
    query_sequence = count(1)

    def search_public_web(query: str, recency: str | None = None) -> str:
        """Search DuckDuckGo for public source candidates; never send internal facts."""

        # recency is accepted only when valid; otherwise omit it rather than guessing.
        from porter_forces_ai.domain import ResearchQuery

        safe_query = egress_policy.validate(query)
        normalized_recency = recency if recency in {"d", "w", "m", "y"} else None
        query_id = f"Q-agent-{next(query_sequence):04d}"
        request = ResearchQuery(
            query_id=query_id,
            query=safe_query,
            rationale="Agent-requested public evidence discovery",
            recency=normalized_recency,
        )
        hits = search.search(request)
        return json.dumps(
            [
                {
                    "query_id": hit.query_id or query_id,
                    "title": hit.title,
                    "url": hit.url,
                    "snippet": hit.snippet,
                    "provider": hit.provider,
                    "retrieved_at": hit.retrieved_at.isoformat(),
                    "trust_classification": "untrusted_search_discovery_metadata",
                }
                for hit in hits
            ],
            ensure_ascii=False,
        )

    limits: list[Any] = [
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="error"),
        ToolCallLimitMiddleware(
            tool_name="search_public_web",
            run_limit=5,
            # A parallel model response can contain more calls than remain in
            # the budget. Execute only the permitted calls and feed explicit
            # limit errors back so the model can still emit ResearchBundle.
            exit_behavior="continue",
        ),
        _single_search_batch_middleware(),
        _research_tool_boundary_middleware(),
    ]

    return create_deep_agent(
        model=model,
        tools=[search_public_web],
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        middleware=limits,
        subagents=[],
        response_format=ResearchBundle,
        backend=StateBackend(),
        name="porter_force_researcher",
    )
