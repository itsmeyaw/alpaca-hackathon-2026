# ADR-0011: Separate Alpaca Runtime and Agent Tool Authority

- Status: Accepted
- Date: 2026-08-27

## Context

The project should visibly use Alpaca's SDK, MCP server, and CLI without allowing an LLM to bypass deterministic execution controls. The always-on service also needs streaming and reconciliation behavior that is better expressed directly through the Python SDK.

## Decision

Assign each Alpaca integration a distinct role:

- Use `alpaca-py` for runtime market streams, trading updates, account reconciliation, historical requests, and order submission.
- Give the Bedrock-hosted LLM read-oriented Alpaca MCP tools for structured research and explainable demonstrations.
- Do not expose a direct MCP order tool to the LLM. Only the Risk and Execution module can translate an approved Trade Intent into an Order Plan.
- Use the Alpaca CLI for operator inspection, dry runs, health checks, and the judging demonstration.

## Consequences

- MCP cannot become a second, weakly governed execution path.
- SDK and MCP observations need timestamps and provenance when included in Decision Records.
- The demo can show all three Alpaca surfaces without misrepresenting which component owns execution.
- Integration tests need fake or paper implementations at the SDK and MCP boundaries.
