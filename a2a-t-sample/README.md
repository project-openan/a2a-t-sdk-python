# a2a-t-sample

`a2a-t-sample` is the sample case collection of the A2A-T Python SDK, organized as one independent directory per case, with runnable client and server entry points.

The current samples run a real A2A `HTTP+JSON/REST` chain based on the official Python A2A SDK (`a2a-sdk`):
- the `a2a-t-sdk` client only generates structured prompts
- the `a2a-t-sdk` server only validates structured prompts

See the repository root [`README.md`](../README.md) for setup and how to run.

## Case List

| Directory | Description |
| --- | --- |
| [fault-management/](fault-management/) | Event subscription sample — stream Incident notifications |
| [ran-energy-saving/](ran-energy-saving/) | RAN energy saving sample — run a RAN energy-saving task, including Negotiation-T negotiations in three kinds (information / target / feasibility) over both generation channels (fromData / fromText), plus an optional webhook transport (`--transport webhook`) that delivers every task event as an A2A push-notification callback to a client-embedded receiver |
| [negotiation/](negotiation/) | Negotiation closed-loop sample — offline propose → accept round trips |
