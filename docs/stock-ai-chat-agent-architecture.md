# Stock AI Chat Agent Architecture

## 1. Goal

Build a conversational stock AI agent that supports both:

- Normal conversation
- Stock-related questions
- Mixed messages containing both normal conversation and stock questions
- Follow-up questions that depend on previous stock context
- Natural switching between general and stock topics

The key principle is:

> Every message first goes through a Conversation/Intent layer. Stock intelligence is activated only when the request requires it.

---

## 2. High-Level Architecture

```text
                         User Message
                              |
                              v
                    +-------------------+
                    | Conversation Router|
                    +---------+---------+
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
        Normal Chat      Stock Related    Mixed Intent
             |                |                |
             v                v                v
        General LLM      Stock Pipeline    Hybrid Pipeline
                              |                |
                              +--------+-------+
                                       |
                                       v
                              Task Orchestrator
                                       |
                    +------------------+------------------+
                    |                  |                  |
                    v                  v                  v
              General LLM        Stock Agent       Research Agent
                                          |
                                          v
                                      Stock Tools
                                          |
                       +------------------+------------------+
                       |                  |                  |
                       v                  v                  v
                  Market Data     Technical Analysis    Fundamentals
                       |                  |                  |
                       +------------------+------------------+
                                          |
                                          v
                                  Response Generator
                                          |
                                          v
                                         User
```

---

## 3. Router Responsibilities

### 3.1 Conversation Router

The first router determines the broad domain of the request:

```text
GENERAL
STOCK
MIXED
AMBIGUOUS
```

Examples:

```text
"How are you?"
-> GENERAL

"What's FPT's RSI?"
-> STOCK

"How was your day? By the way, should I buy FPT?"
-> MIXED
```

The router should not directly answer the user. It should produce a structured routing decision.

Example:

```json
{
  "domain": "stock",
  "requires_ticker": true,
  "requires_market_data": true,
  "requires_reasoning": true
}
```

---

## 4. Intent Detection

After broad routing, detect the actual user intent.

### Stock Intent Taxonomy

```text
STOCK
|
+-- price
|   +-- current_price
|   +-- historical_price
|   +-- price_change
|
+-- technical_analysis
|   +-- trend
|   +-- support_resistance
|   +-- RSI
|   +-- MACD
|   +-- moving_average
|
+-- fundamental
|   +-- revenue
|   +-- profit
|   +-- PE
|   +-- PB
|   +-- valuation
|
+-- comparison
|   +-- stock_vs_stock
|   +-- sector_comparison
|
+-- news
|
+-- portfolio
|
+-- investment_decision
```

Example:

```text
"Compare FPT with CMG"

intent = comparison
tickers = [FPT, CMG]
```

---

## 5. Ticker Resolver

Ticker/entity resolution converts user language into a canonical instrument.

```text
"FPT"
"FPT Corp"
"cổ FPT"
"FPT.HOSE"
"ông FPT"
        |
        v
      FPT
        |
        v
  FPT.HOSE / instrument_id
```

Example output:

```json
{
  "ticker": "FPT",
  "exchange": "HOSE",
  "instrument_id": "12345"
}
```

The system should not depend on the LLM to reliably normalize ticker identifiers every time.

---

## 6. Context Resolver and Memory

Memory should be available before final execution because many stock questions are follow-ups.

### 6.1 Short-Term Conversation Memory

Used for immediate context.

```text
User: Analyze FPT.
AI: ...
User: What about RSI?
```

The system resolves:

```text
What about RSI?
        |
        v
Previous ticker = FPT
        |
        v
Resolved request = RSI of FPT
```

### 6.2 Long-Term User Memory

Store stable preferences such as:

```json
{
  "preferred_market": "VN",
  "favorite_tickers": ["FPT", "VCB", "MWG"],
  "preferred_analysis": "technical"
}
```

### 6.3 Analytical Memory

Store reusable analytical outputs where appropriate:

```text
FPT
Date = 2026-09-05
RSI14 = 62
EMA20 = ...
SMA50 = ...
Trend = bullish
```

Structured market/indicator data should remain in structured data systems rather than being treated as conversational memory.

---

## 7. Handling Normal Conversation + Stock Conversation

The system should not force the user into a separate "Stock Mode".

### Case A: Normal Conversation

```text
User: How are you?

Router -> GENERAL
-> General LLM
```

No stock tools are called.

### Case B: Pure Stock Conversation

```text
User: What's FPT's RSI?

Router -> STOCK
Intent -> technical_indicator
Ticker -> FPT
Tool -> RSI
Response -> LLM
```

### Case C: Mixed Intent

```text
User:
"How was your day? By the way, do you think FPT is a good buy?"
```

Decompose into tasks:

```json
{
  "segments": [
    {
      "intent": "general_conversation"
    },
    {
      "intent": "investment_decision",
      "ticker": "FPT"
    }
  ]
}
```

Task Orchestrator executes both tasks and Response Generator combines them naturally.

---

## 8. Topic Switching

Example:

```text
User: I just finished work.
AI: Nice! Hope you get some rest.
User: Yeah. What about FPT?
```

The resolver detects that `FPT` is a stock entity and activates the stock pipeline.

Then:

```text
current_topic = FPT
stock_context.active = true
```

Later:

```text
User: Anyway, what's Docker?
```

The system switches back to general conversation.

The active stock context does not need to be deleted; it can remain in memory for later follow-up questions.

---

## 9. Intent vs Execution Plan

Separate what the user wants from how the system executes it.

Example:

```text
Intent:
"Analyze FPT"
```

Then a Planner can generate:

```json
{
  "steps": [
    "get_price",
    "get_technical_indicators",
    "get_financial_metrics",
    "get_recent_news",
    "analyze",
    "generate_response"
  ]
}
```

This creates a more flexible agent architecture than hard-coding every request directly to an agent.

---

## 10. LLM Router

The LLM Router is different from the Conversation Router.

### Conversation Router

Answers:

> What type of task is this?

### LLM Router

Answers:

> Which model/agent should perform this task?

Example:

```text
Simple conversation
    -> fast/cheap model

Simple stock lookup
    -> lightweight model + tools

Technical analysis
    -> stronger reasoning model

Complex stock research
    -> strong reasoning model + multiple tools
```

This allows cost optimization without reducing capability for complex requests.

---

## 11. Stock Agent and Tool Layer

The Stock Agent should use deterministic tools rather than inventing market data.

```text
                    Stock Agent
                         |
                  +------+------+
                  | Tool Router |
                  +------+------+
                         |
        +----------------+----------------+
        |                |                |
        v                v                v
   Market Data      Technical Tool   Fundamental Tool
        |                |                |
        v                v                v
   Price/OHLCV      RSI/MACD/MA      Revenue/PE/PB
```

Possible tools:

```text
get_stock_price(ticker)
get_ohlcv(ticker, timeframe)
get_indicator(ticker, indicator)
get_financial_metrics(ticker)
get_stock_news(ticker)
search_financial_documents(ticker)
get_portfolio()
```

The LLM reasons over tool results instead of fabricating values.

---

## 12. Context Builder

Do not send the full conversation history to every LLM call.

Build a focused context:

```text
System Context
+
Current User Message
+
Resolved Intent
+
Resolved Ticker
+
Relevant Conversation Memory
+
Relevant User Preferences
+
Market Data
+
Tool Results
+
Previous Answer (when relevant)
```

Example:

```json
{
  "intent": "technical_analysis",
  "ticker": "FPT",
  "timeframe": "1D",
  "user_question": "FPT có đang uptrend không?",
  "market_data": {
    "price": 142000,
    "ema20": 138500,
    "sma50": 133200,
    "sma200": 121000,
    "rsi14": 62
  }
}
```

---

## 13. Suggested Production Service Architecture

```text
                         API Gateway
                              |
                              v
                    Conversation Service
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
        Context Service   Intent Service   Memory Service
             |                |                |
             +----------------+----------------+
                              |
                              v
                       Ticker Resolver
                              |
                              v
                         LLM Router
                              |
               +--------------+--------------+
               |              |              |
               v              v              v
          General Agent   Stock Agent   Research Agent
                              |
                              v
                         Task Planner
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
     Market Data        Technical Engine     Fundamentals
          |                   |                   |
          +-------------------+-------------------+
                              |
                              v
                     Response Generator
                              |
                              v
                             User
```

---

## 14. Data Storage

### Redis

Use for fast/temporary state:

```text
- Session state
- Short-term conversation context
- Active ticker context
- Market data cache
- Tool result cache
```

### PostgreSQL / Relational DB

Use for structured business data:

```text
- Users
- Conversations
- Portfolios
- Stock metadata
- User preferences
```

### Time-Series Database

Use for:

```text
- OHLCV
- Historical prices
- Technical indicators
```

### Vector DB

Use primarily for semantic retrieval:

```text
- News
- Annual reports
- Financial documents
- Research documents
- Semantic conversation memory where appropriate
```

Do not put every piece of stock data into a vector database.

---

## 15. Complete Request Flow

```text
USER
  |
  v
Conversation Router
  |
  v
Context Resolver
  |
  +---- Conversation Memory
  |
  +---- User Memory
  |
  v
Intent + Entity Extraction
  |
  +---- Intent
  |
  +---- Ticker
  |
  +---- Timeframe
  |
  v
Task Planner
  |
  v
LLM Router
  |
  +-------------------+
  |                   |
  v                   v
General Agent      Stock Agent
                       |
                       v
                   Tool Calling
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
      Market           TA         Fundamentals
        |              |              |
        +--------------+--------------+
                       |
                       v
                   Reasoning
                       |
                       v
               Response Generator
                       |
                       v
                 Memory Update
                       |
                       v
                      USER
```

---

## 16. Key Design Principles

1. **Do not make the LLM the only router.** Use deterministic services where possible.

2. **Do not force Chat Mode vs Stock Mode.** Let the system switch naturally based on the current request and context.

3. **Separate intent from execution.** `investment_decision` is an intent; the planner decides which data/tools are needed.

4. **Resolve context before execution.** Follow-up questions such as `"What about RSI?"` need the previous ticker.

5. **Separate stock data from conversational memory.** Price/RSI/PE are structured data, not memories.

6. **Use specialized tools for facts.** Market and financial data should come from trusted data services.

7. **Use an LLM Router for cost and capability optimization.** Simple tasks can use lightweight models; complex analysis can use stronger reasoning models.

8. **Use a Response Generator at the end.** This makes mixed requests feel like one natural conversation.

---

## 17. Recommended Conceptual Model

The most important abstraction is not:

```text
"Is this a stock question?"
```

It is:

```text
"What tasks are present in this message,
what context do they depend on,
and which agent/tool should execute each task?"
```

That gives a natural conversational experience across:

```text
"Hi"
"What's FPT price?"
"Analyze FPT"
"What about RSI?"
"Compare it with VCB"
"Thanks. What's Docker?"
"Anyway, should I buy FPT now?"
```

without requiring the user to explicitly switch modes.

---

## 14. Out of Scope and Safety Boundary

The agent should explicitly distinguish between:

```text
SUPPORTED
UNSUPPORTED
OUT_OF_SCOPE
AMBIGUOUS
```

The goal is not to reject an entire message just because one part is unsupported. For mixed requests, decompose the message and handle each task independently.

### 14.1 Out-of-Scope Categories

Typical out-of-scope requests include:

```text
OUT OF SCOPE
|
+-- Unsupported markets/assets
|   +-- Crypto, forex, commodities, etc. when not supported
|
+-- Trade execution
|   +-- "Buy 1,000 FPT shares for me"
|   +-- "Sell my VCB position"
|
+-- Brokerage/account operations
|   +-- Place orders
|   +-- Transfer money
|   +-- Access or change brokerage credentials
|
+-- Requests requiring unavailable/private data
|   +-- Private company information
|   +-- Non-authorized account data
|
+-- Completely unrelated requests outside the product domain
|   +-- Tasks not supported by the general assistant either
|
+-- Requests requiring unsupported capabilities
    +-- Actions the system has no tool/integration to execute
```

### 14.2 Out-of-Scope Routing

Do not send every unsupported request directly to an error response. First determine whether the message contains a supported task that can still be completed.

```text
                         User Message
                              |
                              v
                    Conversation Router
                              |
                              v
                     Intent Decomposer
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
         GENERAL          SUPPORTED        OUT OF SCOPE
             |                |                |
             v                v                v
        General LLM      Agent / Tools     Boundary Handler
             |                |                |
             +----------------+----------------+
                              |
                              v
                      Response Generator
```

### 14.3 Mixed Supported + Unsupported Request

Example:

```text
User:
"What do you think about FPT, and can you place an order for 1,000 shares?"
```

Decompose into two tasks:

```json
{
  "tasks": [
    {
      "intent": "investment_analysis",
      "ticker": "FPT",
      "status": "supported"
    },
    {
      "intent": "trade_execution",
      "status": "out_of_scope"
    }
  ]
}
```

The system should:

```text
investment_analysis
    -> Stock Agent
    -> analyze FPT

trade_execution
    -> Boundary Handler
    -> explain that order execution is not supported

                    |
                    v
             Response Generator
                    |
                    v
     Answer supported analysis + clearly state
     execution limitation
```

The unsupported part must not prevent the system from answering the supported part.

### 14.4 Unsupported Stock Question

Example:

```text
User:
"Analyze BTC for me."
```

When crypto is outside the supported product scope:

```text
Intent = asset_analysis
Entity = BTC
Asset Type = crypto
Support Status = unsupported
        |
        v
Boundary Handler
        |
        v
Explain supported asset scope
```

The system should not silently reinterpret BTC as a stock or invent stock-market data.

### 14.5 Ambiguous Requests

Ambiguity is different from out-of-scope.

Example:

```text
User:
"How is Apple doing?"
```

Possible interpretations:

```text
Apple company / AAPL stock / Apple product
```

The system should resolve context first. If context is insufficient, ask a concise clarification rather than selecting a potentially wrong domain.

### 14.6 Boundary Decision Model

The final routing decision can be represented as:

```json
{
  "domain": "stock",
  "intent": "investment_analysis",
  "entities": ["FPT"],
  "support_status": "supported",
  "requires_tools": true,
  "requires_reasoning": true
}
```

For an unsupported operation:

```json
{
  "domain": "stock",
  "intent": "trade_execution",
  "entities": ["FPT"],
  "support_status": "out_of_scope",
  "reason": "trade_execution_not_supported"
}
```

### 14.7 Recommended Boundary Rules

1. **Never fabricate support.** If there is no tool or integration for an action, do not claim that the action was performed.
2. **Partial completion is preferred.** Complete supported subtasks and clearly identify unsupported ones.
3. **Do not fabricate market data.** Missing data should result in a limitation or fallback, not an invented value.
4. **Do not confuse ambiguity with unsupported intent.** Try context resolution first; clarify only when necessary.
5. **Keep the boundary outside the LLM when possible.** Capability checks should be deterministic and auditable.
6. **Use the LLM for explanation, not authorization.** The LLM can explain why a request cannot be performed, but a policy/capability layer should make the actual allow/deny decision.

---

## 15. End-to-End Routing Model

The complete architecture can therefore be summarized as:

```text
USER
  |
  v
Conversation Router
  |
  v
Context Resolver + Memory
  |
  v
Intent + Entity Extraction
  |
  v
Ticker / Entity Resolver
  |
  v
Capability / Scope Check
  |
  +-------------------+-------------------+------------------+
  |                   |                   |                  |
  v                   v                   v                  v
GENERAL           SUPPORTED          MIXED TASK         OUT OF SCOPE
  |                   |                   |                  |
  v                   v                   v                  v
General LLM       LLM Router       Task Planner       Boundary Handler
                      |               |       |
                      v               v       v
                Stock/Research    General   Stock
                    Agent           Agent    Agent
                      |               |       |
                      v               +-------+
                  Tool Layer            |
                      |                 |
                      +--------+--------+
                               v
                       Response Generator
                               |
                               v
                              USER
```

This provides a clean separation between **conversation**, **intent**, **capability**, **execution**, and **response generation**, while still allowing natural conversations that move freely between general questions and stock analysis.
