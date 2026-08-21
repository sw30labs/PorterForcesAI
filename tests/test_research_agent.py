import asyncio
import json
from typing import Any

import httpx
import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from porter_forces_ai.domain import SearchHit
from porter_forces_ai.egress import EgressPolicy, EgressViolation
from porter_forces_ai.research_agent import (
    _DEEP_AGENT_BUILTIN_TOOLS,
    ResearchAgentPolicyError,
    _has_successful_search_result,
    _reject_disallowed_tool_calls,
    create_force_research_agent,
)


class FakeSearch:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def search(self, request: Any) -> list[SearchHit]:
        self.requests.append(request)
        return [
            SearchHit(
                query_id=request.query_id,
                title="Public source",
                url="https://example.org/report",
                snippet="Discovery text",
                provider="duckduckgo",
            )
        ]

    async def asearch(self, request: Any) -> list[SearchHit]:
        return self.search(request)


def _model(name: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=name,
        api_key="local-test-key",
        base_url="http://127.0.0.1:8000/v1",
    )


def _structured_bundle() -> dict[str, Any]:
    return {
        "force": "competitive_rivalry",
        "hypotheses": [
            {
                "hypothesis_id": "H-rivalry",
                "force": "competitive_rivalry",
                "driver": "Competitor investment",
                "force_effect": "Raises competitive pressure",
                "economic_mechanism": "Compresses differentiation advantages",
                "organization_exposure": "Increases the cost of delayed learning",
                "observable_signals": ["Competitor deployment disclosures"],
                "falsification_condition": "Competitors stop scaling deployments",
            }
        ],
        "queries": [
            {
                "query_id": "Q-rivalry",
                "force": "competitive_rivalry",
                "query": "bank AI competitor deployment disclosures",
                "rationale": "Test competitive investment intensity",
                "preferred_source_classes": [],
                "recency": None,
            }
        ],
        "source_candidates": [],
        "model_priors": [],
        "evidence_gaps": ["No verified deployment economics found"],
    }


def _tool_response(model: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-bounded-research",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _search_tool_call(index: int) -> dict[str, Any]:
    return {
        "id": f"call-search-{index}",
        "type": "function",
        "function": {
            "name": "search_public_web",
            "arguments": json.dumps({"query": f"bank AI rivalry public evidence {index}"}),
        },
    }


def _bundle_tool_call() -> dict[str, Any]:
    return {
        "id": "call-structured-output",
        "type": "function",
        "function": {
            "name": "ResearchBundle",
            "arguments": json.dumps(_structured_bundle()),
        },
    }


def test_research_search_tool_enforces_run_policy_and_auditable_ids() -> None:
    search = FakeSearch()
    agent = create_force_research_agent(
        _model("research-policy-contract"),
        search,
        egress_policy=EgressPolicy(forbidden_terms=("Project Cedar",)),
    )
    tools = agent.nodes["tools"].bound._tools_by_name
    public_search = tools["search_public_web"]

    first = json.loads(public_search.invoke({"query": "bank AI competition"}))
    second = json.loads(public_search.invoke({"query": "bank AI suppliers"}))

    assert first[0]["query_id"] == "Q-agent-0001"
    assert second[0]["query_id"] == "Q-agent-0002"
    assert first[0]["provider"] == "duckduckgo"
    with pytest.raises(EgressViolation):
        public_search.invoke({"query": "Project Cedar competitor response"})


def test_guessed_deep_agent_builtin_tool_call_is_rejected() -> None:
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "id"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with pytest.raises(ResearchAgentPolicyError):
        _reject_disallowed_tool_calls(response)


def test_only_successful_search_tool_messages_complete_the_batch() -> None:
    error = ToolMessage(
        content="invalid arguments",
        name="search_public_web",
        tool_call_id="call-error",
        status="error",
    )
    success = ToolMessage(
        content="[]",
        name="search_public_web",
        tool_call_id="call-success",
        status="success",
    )

    assert not _has_successful_search_result([error])
    assert _has_successful_search_result([error, success])


def test_registered_profile_excludes_all_additive_builtin_tools() -> None:
    from deepagents.profiles.harness.harness_profiles import (
        _harness_profile_for_model,
    )

    model = _model("research-profile-contract")
    create_force_research_agent(model, FakeSearch(), egress_policy=EgressPolicy())
    profile = _harness_profile_for_model(model, None)

    assert profile.excluded_tools == _DEEP_AGENT_BUILTIN_TOOLS
    assert profile.general_purpose_subagent is not None
    assert profile.general_purpose_subagent.enabled is False


def test_omlx_request_exposes_only_research_and_structured_output_tools() -> None:
    requests: list[dict[str, Any]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_tool_response(
                "research-visible-tools-contract",
                [_search_tool_call(1)] if len(requests) == 1 else [_bundle_tool_call()],
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        model = ChatOpenAI(
            model="research-visible-tools-contract",
            api_key="local-test-key",
            base_url="http://omlx.test/v1",
            http_client=client,
            max_retries=0,
            use_responses_api=False,
        )
        agent = create_force_research_agent(
            model,
            FakeSearch(),
            egress_policy=EgressPolicy(),
        )

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Analyze competitive rivalry for public bank AI adoption.",
                    }
                ]
            }
        )
    finally:
        client.close()

    assert result["structured_response"].force == "competitive_rivalry"
    assert len(requests) == 2
    visible_tools = {
        tool["function"]["name"]
        for tool in requests[0]["tools"]
        if tool.get("type") == "function"
    }
    assert visible_tools == {"search_public_web", "ResearchBundle"}
    assert visible_tools.isdisjoint(_DEEP_AGENT_BUILTIN_TOOLS)


def test_parallel_search_batch_is_capped_but_structured_output_can_complete() -> None:
    requests: list[dict[str, Any]] = []

    def response(tool_calls: list[dict[str, Any]]) -> httpx.Response:
        return httpx.Response(
            200,
            json=_tool_response("bounded-research-contract", tool_calls),
        )

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return response(
                [_search_tool_call(index) for index in range(6)]
            )
        return response([_bundle_tool_call()])

    search = FakeSearch()
    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        model = ChatOpenAI(
            model="bounded-research-contract",
            api_key="local-test-key",
            base_url="http://omlx.test/v1",
            http_client=client,
            max_retries=0,
            use_responses_api=False,
        )
        agent = create_force_research_agent(
            model,
            search,
            egress_policy=EgressPolicy(),
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": "Analyze competitive rivalry."}]}
        )
    finally:
        client.close()

    assert result["structured_response"].force == "competitive_rivalry"
    assert len(search.requests) == 5
    assert len(requests) == 2
    second_visible_tools = {
        tool["function"]["name"]
        for tool in requests[1]["tools"]
        if tool.get("type") == "function"
    }
    assert second_visible_tools == {"ResearchBundle"}
    assert requests[1]["tool_choice"] == "required"


@pytest.mark.parametrize("mixed_with_search", [False, True])
def test_structured_output_before_search_results_is_rejected(
    mixed_with_search: bool,
) -> None:
    requests: list[dict[str, Any]] = []
    search = FakeSearch()
    tool_calls = [_bundle_tool_call()]
    if mixed_with_search:
        tool_calls.insert(0, _search_tool_call(1))

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_tool_response(
                f"early-structured-{mixed_with_search}",
                tool_calls,
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        model = ChatOpenAI(
            model=f"early-structured-{mixed_with_search}",
            api_key="local-test-key",
            base_url="http://omlx.test/v1",
            http_client=client,
            max_retries=0,
            use_responses_api=False,
        )
        agent = create_force_research_agent(
            model,
            search,
            egress_policy=EgressPolicy(),
        )

        with pytest.raises(ResearchAgentPolicyError):
            agent.invoke(
                {"messages": [{"role": "user", "content": "Analyze rivalry."}]}
            )
    finally:
        client.close()

    assert len(requests) == 1
    assert search.requests == []


def test_async_guessed_search_after_completed_batch_is_rejected() -> None:
    async def scenario() -> None:
        requests: list[dict[str, Any]] = []
        search = FakeSearch()

        def handle_request(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            tool_calls = (
                [_search_tool_call(index) for index in range(3)]
                if len(requests) == 1
                else [_search_tool_call(99)]
            )
            return httpx.Response(
                200,
                json=_tool_response("async-hidden-search-contract", tool_calls),
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle_request)
        ) as client:
            model = ChatOpenAI(
                model="async-hidden-search-contract",
                api_key="local-test-key",
                base_url="http://omlx.test/v1",
                http_async_client=client,
                max_retries=0,
                use_responses_api=False,
            )
            agent = create_force_research_agent(
                model,
                search,
                egress_policy=EgressPolicy(),
            )

            with pytest.raises(ResearchAgentPolicyError):
                await agent.ainvoke(
                    {"messages": [{"role": "user", "content": "Analyze rivalry."}]}
                )

        assert len(requests) == 2
        assert len(search.requests) == 3
        second_visible_tools = {
            tool["function"]["name"]
            for tool in requests[1]["tools"]
            if tool.get("type") == "function"
        }
        assert second_visible_tools == {"ResearchBundle"}

    asyncio.run(scenario())
