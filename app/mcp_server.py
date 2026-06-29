import asyncio
import sys
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

server = Server("compliance-mcp-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_corporate_policies",
            description="Retrieve corporate travel, meals, and subscription expense policies.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="lookup_employee_allowance",
            description="Lookup specific allowance and compliance parameters for an employee by email.",
            inputSchema={
                "type": "object",
                "properties": {
                    "employee_email": {"type": "string", "description": "The employee's work email."}
                },
                "required": ["employee_email"],
            },
        ),
        types.Tool(
            name="log_audit_action",
            description="Log an audit compliance action, warning, or review decision.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action_name": {"type": "string", "description": "The name of the audit action (e.g., FLAG_VIOLATION, EXCEPTION_APPROVED)."},
                    "details": {"type": "string", "description": "Details of the action taken."}
                },
                "required": ["action_name", "details"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if arguments is None:
        arguments = {}
    if name == "get_corporate_policies":
        policies = (
            "Corporate Expense Policies:\n"
            "1. Meal cap: $150 per person per day. Any meal expense exceeding this must be flagged.\n"
            "2. Travel cap: $1000 per flight/trip booking. First-class bookings are strictly prohibited without prior written director approval.\n"
            "3. Software subscriptions: Individual employees are not permitted to buy SaaS software subscriptions on corporate cards. All software must go through IT procurement."
        )
        return [types.TextContent(type="text", text=policies)]
        
    elif name == "lookup_employee_allowance":
        email = arguments.get("employee_email", "")
        if "john.doe" in email.lower():
            allowance = (
                "Employee: John Doe (Sales Exec)\n"
                "Daily Meal Cap: $200 (special client allowance)\n"
                "Travel Tier: Business Class allowed."
            )
        else:
            allowance = f"Employee: {email}\nDaily Meal Cap: $150\nTravel Tier: Economy Class only."
        return [types.TextContent(type="text", text=allowance)]
        
    elif name == "log_audit_action":
        action_name = arguments.get("action_name", "")
        details = arguments.get("details", "")
        print(f"AUDIT LOG - Action: {action_name}, Details: {details}", file=sys.stderr)
        return [types.TextContent(type="text", text=f"Successfully logged audit action: {action_name}")]
        
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="compliance-mcp-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
