Act as an expert Python engineer specializing in local AI agents. Write a complete, runnable Python script that implements a Local Hybrid Memory Agent using LangChain, ChromaDB, and Ollama then integrate into our project

The agent must maintain context across topic switches (e.g., banks vs. construction) using a combination of a sliding conversational window and a semantic vector database.

### 1. Technology Stack
* Orchestration: LangChain (`langchain`, `langchain-community`, `langchain-chroma`)
* LLM Engine: Ollama (Model: `llama3` or `mistral`)
* Embeddings: Ollama (Model: `nomic-embed-text`)
* Vector Database: ChromaDB (Local persistent storage)

### 2. Architecture Requirements
Implement a custom Chain or Agent class that performs the following exact pipeline on every user message:

1. Vector Retrieval: Take the user's incoming query, embed it, and search the ChromaDB vector store for the top 3 most semantically similar past conversational turns.
2. Sliding Window Buffer: Maintain an in-memory buffer of only the last 4 conversational turns (User + AI) to manage the immediate context window limits.
3. Prompt Assembly: Construct a LangChain `ChatPromptTemplate` that strictly orders the context:
   - System prompt (establishing the AI's persona).
   - [Retrieved Long-Term Context] (Insert the results from step 1 here).
   - [Recent Conversation History] (Insert the sliding window buffer from step 2 here).
   - The new User Input.
4. Generation: Pass the assembled prompt to the local Ollama LLM and stream or return the response.
5. Ingestion/Storage: After generation, simultaneously save the new (User Input + AI Response) pair to:
   - The in-memory sliding window buffer.
   - The ChromaDB vector store as a new embedded document (so it can be retrieved in future sessions).

### 3. Code Requirements
* Include necessary imports.
* Define a clear `SystemMessage` template.
* Use `Chroma` from `langchain_chroma` with a persistent local directory (`./chroma_db`).
* Implement a `chat(user_message)` function or method that handles the 5-step pipeline cleanly.
* Add a simple `while True:` CLI loop at the bottom so I can run the script and test the topic switching immediately.
* Add inline comments explaining the hand-off between the sliding window and the vector store.