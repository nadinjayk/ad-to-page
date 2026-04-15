# Design Transfer Documentation

This folder documents the current implementation, not just the original spec.

If you want the fastest path through the docs, read them in this order:

1. [Product Overview](</C:/Users/91956/Desktop/assignment final/docs/product-overview.md>)
2. [Architecture and API Flow](</C:/Users/91956/Desktop/assignment final/docs/architecture-and-api.md>)
3. [Models and Prompting Strategy](</C:/Users/91956/Desktop/assignment final/docs/models-and-prompting.md>)
4. [UI and UX Notes](</C:/Users/91956/Desktop/assignment final/docs/ui-and-ux.md>)
5. [Guardrails and Reliability](</C:/Users/91956/Desktop/assignment final/docs/guardrails-and-reliability.md>)
6. [Operations, Troubleshooting, and Lessons](</C:/Users/91956/Desktop/assignment final/docs/operations-troubleshooting-and-lessons.md>)

## What These Docs Cover

- what the product does end to end
- how the frontend and backend interact
- which models are used in each stage
- how asset search works, including fallbacks
- what UI options exist today
- how guardrails reduce broken HTML, unsafe assets, and hallucinations
- what changed from the original spec
- what local-development issues were encountered and how they were resolved

## Source of Truth

These docs are based on the current codebase in:

- [backend](</C:/Users/91956/Desktop/assignment final/backend>)
- [frontend](</C:/Users/91956/Desktop/assignment final/frontend>)
- [launch_app.bat](</C:/Users/91956/Desktop/assignment final/launch_app.bat>)
- [design_transfer_spec.md](</C:/Users/91956/Desktop/assignment final/design_transfer_spec.md>)

The spec remains useful for context, but the implementation has evolved in a few important ways, especially around:

- simplifying the operator flow
- reducing exposed controls in the UI
- using a single-request generation pattern plus one repair pass
- adding stronger HTML and asset guardrails
- making web asset sourcing optional and reversible
