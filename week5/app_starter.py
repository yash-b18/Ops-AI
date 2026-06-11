"""
Week 5: Agent Architecture Starter Template

Build an AI agent that answers TechCorp questions using:
- Gemini 2.5 Pro LLM (free tier via Google AI API)
- SQLite database queries
- Policy document retrieval

Complete the TODO sections marked below.
"""

import json
import re
import sqlite3
import time
from typing import Dict, Any
import google.genai as genai
from google.genai import types
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env file into the environment.

    The starter only reads os.getenv() and there is no python-dotenv
    dependency, so this lightweight loader lets a week5/.env file work out of
    the box. An already-set environment variable always takes precedence.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")


# TASK 1: Implement the Tool base class


class Tool:
    """Base class for tools the agent can call."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def execute(self, **kwargs) -> str:
        """Execute the tool.

        TODO: This is implemented by subclasses.
        Each subclass should override this method.
        """
        raise NotImplementedError


# TASK 2: Implement EmployeeLookupTool


class EmployeeLookupTool(Tool):
    """Look up employee information from SQLite database."""

    def __init__(self, db_path: str):
        super().__init__("employee_lookup", "Find employee information by name or ID")
        self.db_path = db_path

    def execute(self, employee_name: str = None, employee_id: str = None) -> str:
        """Look up employee by name or ID.

        TODO: Query the employees table:
        1. Connect to SQLite database at self.db_path
        2. If employee_id is provided:
           - SELECT * FROM employees WHERE id = ?
        3. If employee_name is provided:
           - SELECT * FROM employees WHERE name LIKE ?
        4. Convert results to JSON and return
        5. If no results found, return "Employee not found"

        Args:
            employee_name: Name to search for (partial match ok)
            employee_id: ID to search for (exact match)

        Returns:
            JSON string with employee info or error message
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # rows act like dicts: column name -> value
            try:
                cursor = conn.cursor()
                if employee_id is not None:
                    # Exact match on the primary key
                    cursor.execute("SELECT * FROM employees WHERE id = ?", (employee_id,))
                elif employee_name is not None:
                    # Case-insensitive partial match on the name
                    cursor.execute(
                        "SELECT * FROM employees WHERE name LIKE ?",
                        (f"%{employee_name}%",),
                    )
                else:
                    return "Provide an employee_name or employee_id to look up."
                rows = cursor.fetchall()
            finally:
                conn.close()

            if not rows:
                return "Employee not found"

            employees = [dict(row) for row in rows]
            return json.dumps(employees, indent=2, default=str)
        except Exception as e:
            logger.error(f"Employee lookup error: {e}")
            return f"Error: {str(e)}"


# TASK 3: Implement PolicySearchTool


class PolicySearchTool(Tool):
    """Search policy documents by keyword."""

    def __init__(self):
        super().__init__("policy_search", "Search policy documents by keyword or topic")
        # Load the document corpus once at construction so execute() never re-reads disk.
        try:
            with open("data/documents.json") as f:
                self.documents = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load documents.json: {e}")
            self.documents = []

    def execute(self, query: str, limit: int = 5) -> str:
        """Search policies by keyword.

        TODO: Implement policy search:
        1. Load documents (from JSON file in data/ folder)
        2. Search documents by keyword match
        3. Return top-N matching documents
        4. Include title and snippet (first 500 chars) for each

        Args:
            query: Search term
            limit: Max results to return

        Returns:
            Formatted string with matching documents
        """
        try:
            if not self.documents:
                return "No policy documents are available to search."

            q = query.lower()
            # Split the query into meaningful words so multi-word queries like
            # "travel policy" still match "Travel and Expense Policy" (exact
            # substring matching would miss it). Common stop-words are dropped.
            stop = {"the", "is", "a", "an", "of", "to", "for", "and", "what",
                    "does", "do", "in", "on", "my", "our", "are", "how"}
            words = [w for w in re.findall(r"[a-z0-9]+", q) if w not in stop]
            if not words:
                words = re.findall(r"[a-z0-9]+", q) or [q]

            # Score by query-word frequency (title hits weigh more); an exact
            # full-phrase match gets a large bonus so it ranks first when present.
            scored = []
            for doc in self.documents:
                title = doc.get("title", "").lower()
                content = doc.get("content", "").lower()
                score = sum(content.count(w) + 3 * title.count(w) for w in words)
                if q in content or q in title:
                    score += 50
                if score > 0:
                    scored.append((score, doc))

            if not scored:
                return f"No policies found matching '{query}'."

            # Highest-scoring (most relevant) documents first, then keep the top N.
            scored.sort(key=lambda pair: pair[0], reverse=True)

            results = []
            for _, doc in scored[:limit]:
                title = doc.get("title", "(untitled)")
                content = doc.get("content", "")
                snippet = content[:500] + ("..." if len(content) > 500 else "")
                results.append(f"Title: {title}\n{snippet}")

            return "\n\n---\n\n".join(results)
        except Exception as e:
            logger.error(f"Policy search error: {e}")
            return f"Error: {str(e)}"


# TASK 4: Implement ExpenseQueryTool


class ExpenseQueryTool(Tool):
    """Query expense policies and approval limits."""

    def __init__(self):
        super().__init__("expense_query", "Query expense approval limits by role")
        # Load the expense policies once at construction (same pattern as policy_search).
        try:
            with open("data/policies.json") as f:
                self.policies = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load policies.json: {e}")
            self.policies = {}

    def execute(self, role: str) -> str:
        """Query expense approval limit for a given role.

        TODO: Implement expense lookup:
        1. Look up role in self.policies["expense"]["approval_limits"]
        2. Return: "Approval limit for {role}: ${amount}"
        3. If role not found, return "Role not found: {role}"

        Args:
            role: Employee role (ic1_ic2, ic3, manager, director, vp)

        Returns:
            String with approval limit for the given role
        """
        try:
            limits = self.policies["expense"]["approval_limits"]
            if role in limits:
                return f"Approval limit for {role}: ${limits[role]}"
            return f"Role not found: {role}"
        except Exception as e:
            logger.error(f"Expense query error: {e}")
            return f"Error: {str(e)}"


# TASK 5: Implement the Agent class


class Agent:
    """AI agent that answers questions using Gemini LLM + tools."""

    def __init__(self, db_path: str, api_key: str = None):
        """Initialize the agent.

        TODO:
        1. Get API key from parameter or GOOGLE_API_KEY environment variable
        2. Raise ValueError if no API key provided
        3. Initialize Google GenAI client with api_key
        4. Initialize all tools (EmployeeLookup, PolicySearch, ExpenseQuery)
        5. Initialize token and cost tracking variables

        Args:
            db_path: Path to SQLite database
            api_key: Google AI API key (or use GOOGLE_API_KEY env var)
        """
        self.db_path = db_path
        self.api_key = api_key or GOOGLE_API_KEY

        if not self.api_key:
            raise ValueError(
                "GOOGLE_API_KEY not set. Get free key at: "
                "https://aistudio.google.com/app/apikey"
            )

        # Gemini client. Use a free-tier "flash" model instead of the paid "pro"
        # model named in the README to keep per-query cost near zero. Flash-Lite
        # is used because gemini-2.5-flash's free tier is capped at only ~20
        # requests/day, which a session of testing exhausts quickly.
        self.client = genai.Client(api_key=self.api_key)
        self.model = "gemini-2.5-flash-lite"

        # Register the tools the agent can call, keyed by name.
        self.tools = {
            "employee_lookup": EmployeeLookupTool(db_path),
            "policy_search": PolicySearchTool(),
            "expense_query": ExpenseQueryTool(),
        }

        # Running usage metrics.
        self.token_count = 0
        self.total_cost = 0.0
        self.queries_run = 0

        # How many times to retry a transient (429/503) API error.
        self.max_retries = 5

    def _build_system_prompt(self, user_role: str) -> str:
        """Build system prompt describing available tools.

        TODO: Create a prompt that:
        1. Describes the agent's purpose
        2. Lists all available tools with descriptions
        3. Explains how to use them
        4. Sets the user's role context

        Returns:
            System prompt string
        """
        tool_descriptions = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )
        return (
            "You are TechCorp's internal assistant. Answer employee questions about "
            "people, company policies, and expense approval limits using the tools below.\n\n"
            f"User role: {user_role}\n\n"
            "Available tools:\n"
            f"{tool_descriptions}\n\n"
            "Tool arguments:\n"
            "- employee_lookup: employee_name=<full or partial name>  OR  employee_id=<number>\n"
            "- policy_search: query=<keywords or topic>   (optional: limit=<number>)\n"
            "- expense_query: role=<one of: ic1_ic2, ic3, manager, director, vp>\n\n"
            "When a tool is needed, respond with EXACTLY this and nothing else:\n"
            "TOOL: <tool_name>\n"
            "ARGS: <key>=<value>\n"
            "(use one ARGS line per argument). Otherwise, answer the question directly "
            "in plain text with no TOOL line."
        )

    def query(self, user_query: str, user_role: str = "engineer") -> Dict[str, Any]:
        """Answer a question using LLM + tools.

        TODO: Implement the reasoning loop:

        1. Call _build_system_prompt(user_role) to build the system prompt

        2. Call Gemini LLM with system prompt + user question
           - self.client.models.generate_content(model="gemini-2.5-pro", ...)

        3. Parse LLM response to identify tool calls
           - Check if response mentions any tool names
           - Extract parameters from response

        4. Execute tools with extracted parameters
           - tool.execute() with parameters
           - Collect results

        5. Synthesize final answer
           - Pass tool results back to LLM
           - Get final answer

        6. Track tokens and cost
           - Count tokens in request/response
           - Calculate cost: (tokens / 1_000_000) * rate
           - Update totals

        Args:
            user_query: The question to answer
            user_role: User's role (for access control in future weeks)

        Returns:
            Dict with keys:
            - "answer": str - the response
            - "tokens_used": int - total tokens
            - "cost": float - cost in dollars
            - "role": str - user role
        """
        logger.info(f"Processing query: {user_query}")

        system_prompt = self._build_system_prompt(user_role)
        input_tokens = 0
        output_tokens = 0

        try:
            # Steps 1-2: ask the LLM what to do with the question.
            routing = self._generate(user_query, system_prompt)
            in_tok, out_tok = self._token_counts(routing)
            input_tokens += in_tok
            output_tokens += out_tok

            # Step 3: did the LLM ask for a tool?
            tool_name, tool_args = self._parse_tool_call(routing.text or "")

            if tool_name is None:
                # Step 5 (early exit): no tool needed, the reply is the answer.
                answer = (routing.text or "").strip()
            else:
                # Step 4: run the requested tool (gracefully handle unknown tools).
                if tool_name not in self.tools:
                    tool_result = f"(No tool named '{tool_name}' is available.)"
                else:
                    tool_result = self.tools[tool_name].execute(**tool_args)

                # Step 5: feed the tool result back for a grounded final answer.
                synthesis = (
                    f"User question: {user_query}\n\n"
                    f"Result from the '{tool_name}' tool:\n{tool_result}\n\n"
                    "Answer the user's question using only the tool result above."
                )
                final = self._generate(
                    synthesis,
                    "You are TechCorp's assistant. Use the provided tool "
                    "result to answer the question clearly and concisely.",
                )
                in_tok, out_tok = self._token_counts(final)
                input_tokens += in_tok
                output_tokens += out_tok
                answer = (final.text or "").strip()

        except Exception as e:
            logger.error(f"Query failed: {e}")
            answer = f"Sorry, I couldn't complete that request ({e})."

        # Step 6: track tokens and cost (runs even if a call failed above).
        total_tokens = input_tokens + output_tokens
        cost = self._estimate_query_cost(input_tokens, output_tokens)
        self.token_count += total_tokens
        self.total_cost += cost
        self.queries_run += 1

        return {
            "answer": answer,
            "tokens_used": total_tokens,
            "cost": cost,
            "role": user_role,
        }

    def _generate(self, contents: str, system_instruction: str):
        """Call the model, retrying transient 429/503 errors with backoff.

        Honors the server's suggested retry delay when present, otherwise uses
        exponential backoff. Non-transient errors are re-raised immediately;
        after max_retries the last error propagates to query()'s handler.
        """
        for attempt in range(self.max_retries):
            try:
                return self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction
                    ),
                )
            except Exception as e:
                msg = str(e)
                transient = any(
                    code in msg
                    for code in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")
                )
                if not transient or attempt == self.max_retries - 1:
                    raise
                match = re.search(r"retry in ([0-9.]+)s", msg)
                wait = float(match.group(1)) + 1.0 if match else min(5 * 2**attempt, 60.0)
                logger.warning(
                    f"Transient API error (attempt {attempt + 1}/{self.max_retries}); "
                    f"retrying in {wait:.0f}s"
                )
                time.sleep(wait)

    @staticmethod
    def _token_counts(response) -> tuple:
        """Return (input_tokens, output_tokens) from a response's usage metadata.

        output_tokens is derived as total - input so it also captures the
        "thinking" tokens that gemini-2.5 models bill as output but leave out
        of candidates_token_count.
        """
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return 0, 0
        in_tok = usage.prompt_token_count or 0
        total = usage.total_token_count or in_tok
        return in_tok, max(total - in_tok, 0)

    def _parse_tool_call(self, text: str):
        """Parse a 'TOOL:/ARGS:' directive from the LLM response.

        Returns (tool_name, args_dict), or (None, {}) when no tool was requested.
        Digit-only argument values are coerced to int (e.g. limit, employee_id).
        """
        tool_name = None
        args = {}
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("TOOL:"):
                tool_name = line.split(":", 1)[1].strip()
            elif line.upper().startswith("ARGS:"):
                arg_part = line.split(":", 1)[1].strip()
                if "=" in arg_part:
                    key, _, value = arg_part.partition("=")
                    value = value.strip()
                    args[key.strip()] = int(value) if value.isdigit() else value
        if not tool_name or tool_name.lower() in ("none", "n/a"):
            return None, {}
        return tool_name, args

    def _estimate_query_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost based on tokens.

        Gemini 2.5 Flash-Lite pricing (paid tier; the free tier used here is $0,
        so this estimates what the same usage would cost at scale):
        - Input:  $0.10 per 1M tokens
        - Output: $0.40 per 1M tokens (includes any "thinking" tokens, which
          _token_counts() folds into output via total - input)
        """
        input_cost = (input_tokens / 1_000_000) * 0.10
        output_cost = (output_tokens / 1_000_000) * 0.40
        return input_cost + output_cost

    def get_metrics(self) -> Dict[str, Any]:
        """Return performance metrics.

        TODO: Return dict with:
        - total_queries: number of queries processed
        - total_tokens: cumulative tokens used
        - total_cost: cumulative cost in dollars
        - avg_cost_per_query: average cost per query
        """
        return {
            "total_queries": self.queries_run,
            "total_tokens": self.token_count,
            "total_cost": self.total_cost,
            "avg_cost_per_query": (
                self.total_cost / self.queries_run if self.queries_run else 0.0
            ),
        }


# TASK 6: Test your implementation

if __name__ == "__main__":
    """Quick test of agent functionality."""
    import sys

    try:
        # Initialize agent
        agent = Agent("data/techcorp.db")
        print("Agent initialized successfully")

        # Test a query
        print("\nTesting query: 'What is the travel policy?'")
        result = agent.query("What is the travel policy?")
        print(f"Answer: {result['answer']}")
        print(f"Tokens: {result['tokens_used']}")
        print(
            f"Estimated paid-tier cost: ${result['cost']:.6f}  "
            "(actual free-tier cost: $0.00)"
        )

        # Check metrics
        metrics = agent.get_metrics()
        print(f"\nMetrics: {metrics}")
        print(
            "(total_cost / avg_cost_per_query above are estimated paid-tier "
            "costs; on the free tier the actual charge is $0.00)"
        )

    except Exception as e:
        print(f"Error: {e}")
        logger.exception("Error during test")
        sys.exit(1)
