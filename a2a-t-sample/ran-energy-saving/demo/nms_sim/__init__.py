"""Local simulator of the vendor NMS A2A surface.

The simulator reproduces the wire behavior the client must handle (never-closing SSE,
heartbeats, plain-message negotiation, no AgentCard) while reusing the sample's deterministic RAN
data and tools. It is a test/demo asset, not a production NMS.
"""
